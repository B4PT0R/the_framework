"""Versioned client-surface builds, previews, publication and rollback."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import signal
import shutil
import tempfile
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from modict import modict

from ...utils.persistence import MappingStore
from .previews import PreviewAccess


class ClientSurface(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str = "main"
    source: str | Path
    build: tuple[str, ...] = ("npm", "run", "build")
    artifact: str | Path = "dist"
    routes: tuple[str, ...] = ("/ui",)
    server_routes: tuple[str, ...] = ()
    preview_route: str | None = None
    shell: str | None = "playwright"
    retain_releases: int = 3
    timeout_seconds: float | int = 300

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError("surface name must be a Python-style identifier")
        if not self.build or any(not isinstance(value, str) or not value for value in self.build):
            raise ValueError("surface build command must be a nonempty argv tuple")
        if self.artifact.is_absolute() or ".." in self.artifact.parts:
            raise ValueError("surface artifact must stay inside its isolated build root")
        if not self.routes or any(not route.startswith("/") for route in self.routes):
            raise ValueError("surface routes must be absolute application paths")
        if len(self.server_routes) != len(set(self.server_routes)) or any(
            not path.startswith("/")
            or not any(
                path.startswith(f"{route.rstrip('/')}/")
                for route in self.routes
            )
            for path in self.server_routes
        ):
            raise ValueError(
                "surface server routes must be distinct paths below a surface route"
            )
        if self.retain_releases < 2:
            raise ValueError("surface release retention must keep rollback available")
        if self.timeout_seconds <= 0:
            raise ValueError("surface build timeout must be positive")

    @modict.any_validator(mode="before")
    def normalize_paths(self, key, value):
        if key == "source":
            return Path(value).expanduser().resolve()
        if key == "artifact":
            return Path(value)
        if key in {"build", "routes", "server_routes"}:
            return tuple(value)
        if key == "preview_route" and value is None:
            return f"/__surface-preview/{self.name}"
        return value


class SurfaceRelease(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    surface: str
    release: str
    previous: str | None = None


class SurfaceService:
    """Build surfaces off-loop and atomically select immutable releases."""

    def __init__(self, surfaces, root, *, notify=None, progress=None, origin=None):
        surfaces = tuple(surfaces)
        self.surfaces = {surface.name: surface for surface in surfaces}
        if len(self.surfaces) != len(surfaces):
            raise ValueError("duplicate client surface")
        self.root = Path(root).expanduser().resolve()
        self.releases_root = self.root / "releases"
        self.work_root = self.root / "work"
        self.store = MappingStore(self.root / "state.json", field="surfaces")
        self.notify = notify
        self.progress = progress
        self.state = self.store.load()
        self.processes = {}
        self.building = set()
        self.previews = PreviewAccess(self, origin=origin)
        self._validate_state()
        self.candidates = {name: value["candidate"] for name, value in self.state.items() if value.get("candidate")}

    def _validate_state(self):
        cleaned = {}
        for name, value in self.state.items():
            if name not in self.surfaces or not isinstance(value, dict):
                continue
            cleaned[name] = {
                key: value.get(key) if self._release_exists(name, value.get(key)) else None
                for key in ("active", "previous", "candidate")
            }
            if cleaned[name]["active"] is None and cleaned[name]["previous"] is not None:
                cleaned[name]["active"] = cleaned[name]["previous"]
                cleaned[name]["previous"] = None
        self.state = cleaned

    def _release_exists(self, name, release):
        return (
            isinstance(release, str)
            and re.fullmatch(r"[0-9a-f]{64}", release) is not None
            and (self.release_path(name, release) / "index.html").is_file()
        )

    def release_path(self, surface, release):
        return self.releases_root / surface / release

    def active_path(self, surface):
        release = self.state.get(surface, {}).get("active")
        return self.release_path(surface, release) if release else None

    def preview_path(self, surface, release=None):
        release = release or self.candidates.get(surface) or self.state.get(surface, {}).get("active")
        if release is None:
            raise LookupError(f"surface has no preview candidate: {surface}")
        if not self._release_exists(surface, release):
            raise LookupError(f"surface release does not exist: {surface}/{release}")
        return self.release_path(surface, release)

    def status(self):
        """One release view shared by agent controls and HTTP clients."""
        return {"surfaces": [{
            "name": name,
            "active": self.state.get(name, {}).get("active"),
            "previous": self.state.get(name, {}).get("previous"),
            "candidate": self.candidates.get(name),
            "building": name in self.building,
            "preview_route": surface.preview_route,
            "routes": list(surface.routes),
        } for name, surface in self.surfaces.items()]}

    async def initialize(self, *, ui_root=None):
        """Adopt compiled artifacts only where no published release is available.

        Run before accepting requests. The declared source/artifact is the default;
        ui_root can supply an external main bundle (for packaged applications).
        Missing initial artifacts leave the surface unpublished, not the chat down.
        """
        for name, surface in self.surfaces.items():
            if self.active_path(name) is not None:
                continue
            artifact = Path(ui_root) if name == "main" and ui_root is not None else surface.source / surface.artifact
            if (artifact / "index.html").is_file():
                await asyncio.to_thread(self.bootstrap, name, artifact)

    def _store_release(self, name, artifact):
        """Copy and hash a complete snapshot before exposing an immutable release."""
        parent = self.releases_root / name
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".incoming-", dir=parent) as temporary:
            snapshot = Path(temporary) / "content"
            shutil.copytree(artifact, snapshot)
            if not (snapshot / "index.html").is_file():
                raise RuntimeError("surface artifact has no index.html")
            release_id = self._hash_tree(snapshot)
            release = self.release_path(name, release_id)
            if not release.exists():
                snapshot.rename(release)
        return release_id

    async def _progress(self, payload):
        if self.progress is None:
            return
        result = self.progress(payload)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    def _hash_tree(root):
        digest = hashlib.sha256()
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            digest.update(b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _copy_source(source, destination):
        if not source.is_dir():
            raise RuntimeError(f"surface source does not exist: {source}")
        ignored = shutil.ignore_patterns(
            ".git", ".pytest_cache", ".mypy_cache", "__pycache__",
            ".venv", "node_modules", ".surface-build",
            ".dist-renderer-build", "dist-renderer",
        )
        dependency_directories = []
        for root, directories, _files in os.walk(source):
            if "node_modules" in directories:
                dependency_directories.append(Path(root) / "node_modules")
                directories.remove("node_modules")
            directories[:] = [
                value for value in directories
                if value not in {".git", ".venv", ".surface-build"}
            ]
        shutil.copytree(source, destination, symlinks=True, ignore=ignored)
        for dependencies in dependency_directories:
            relative = dependencies.relative_to(source)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(dependencies, target_is_directory=True)

    async def build(self, name):
        surface = self.surfaces.get(name)
        if surface is None:
            raise ValueError(f"unknown client surface: {name}")
        if name in self.building:
            raise RuntimeError(f"surface build already running: {name}")
        self.building.add(name)
        workspace = None
        try:
            self.work_root.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=self.work_root))
            source = workspace / "source"
            await self._progress({"surface": name, "phase": "copying"})
            await asyncio.to_thread(self._copy_source, surface.source, source)
            artifact = source / surface.artifact
            argv = tuple(
                str(artifact) if value == "{artifact}" else value
                for value in surface.build
            )
            await self._progress({"surface": name, "phase": "building"})
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=source,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self.processes[name] = process
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), surface.timeout_seconds
                )
            except TimeoutError:
                await self._terminate(process)
                raise RuntimeError(f"surface build timed out: {name}") from None
            if process.returncode:
                detail = (stdout + b"\n" + stderr).decode(errors="replace")[-8000:]
                raise RuntimeError(
                    f"surface build failed ({process.returncode}): {detail}"
                )
            if not (artifact / "index.html").is_file():
                raise RuntimeError("surface artifact has no index.html")
            release_id = await asyncio.to_thread(self._store_release, name, artifact)
            next_state = {
                **self.state,
                name: {**self.state.get(name, {}), "candidate": release_id},
            }
            self.store.save(next_state)
            self.state = next_state
            self.candidates[name] = release_id
            await self._progress({
                "surface": name,
                "phase": "ready",
                "release": release_id,
                "stdout": stdout.decode(errors="replace")[-4000:],
            })
            return SurfaceRelease(surface=name, release=release_id)
        except asyncio.CancelledError:
            process = self.processes.get(name)
            if process is not None and process.returncode is None:
                await self._terminate(process)
            await self._progress({"surface": name, "phase": "cancelled"})
            raise
        except Exception:
            await self._progress({"surface": name, "phase": "failed"})
            raise
        finally:
            self.processes.pop(name, None)
            self.building.discard(name)
            if workspace is not None:
                await asyncio.to_thread(shutil.rmtree, workspace, True)

    @staticmethod
    async def _terminate(process):
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            await process.wait()

    def bootstrap(self, name, artifact):
        """Adopt an existing verified artifact when no release is active yet."""
        if name not in self.surfaces:
            raise ValueError(f"unknown client surface: {name}")
        active = self.active_path(name)
        if active is not None:
            return SurfaceRelease(surface=name, release=active.name)
        artifact = Path(artifact).expanduser().resolve()
        if not (artifact / "index.html").is_file():
            raise RuntimeError("surface artifact has no index.html")
        release_id = self._store_release(name, artifact)
        next_state = {
            **self.state,
            name: {**self.state.get(name, {}), "active": release_id, "previous": None},
        }
        self.store.save(next_state)
        self.state = next_state
        return SurfaceRelease(surface=name, release=release_id)

    async def cancel(self, name):
        process = self.processes.get(name)
        if process is None or process.returncode is not None:
            return False
        await self._terminate(process)
        return True

    async def _notify(self, payload):
        if self.notify is None:
            return
        result = self.notify(payload)
        if asyncio.iscoroutine(result):
            await result

    async def publish(self, name, release=None):
        if name not in self.surfaces:
            raise ValueError(f"unknown client surface: {name}")
        release = self.preview_path(name, release).name
        current = self.state.get(name, {})
        previous = current.get("active")
        if release == previous:
            return SurfaceRelease(
                surface=name, release=release, previous=current.get("previous")
            )
        next_state = {
            **self.state,
            name: {**current, "active": release, "previous": previous},
        }
        self.store.save(next_state)
        self.state = next_state
        await self._notify({
            "type": "surface_published",
            "surface": name,
            "release": release,
        })
        await self._prune(name)
        return SurfaceRelease(surface=name, release=release, previous=previous)

    async def rollback(self, name):
        current = self.state.get(name, {})
        previous = current.get("previous")
        if previous is None:
            raise RuntimeError(f"surface has no rollback release: {name}")
        active = current.get("active")
        next_state = {
            **self.state,
            name: {**current, "active": previous, "previous": active},
        }
        self.store.save(next_state)
        self.state = next_state
        await self._notify({
            "type": "surface_rolled_back",
            "surface": name,
            "release": previous,
        })
        return SurfaceRelease(
            surface=name, release=previous, previous=active
        )

    async def _prune(self, name):
        surface = self.surfaces[name]
        protected = set(self.state.get(name, {}).values()) | {
            self.candidates.get(name)
        }
        releases = sorted(
            (path for path in (self.releases_root / name).glob("*") if path.is_dir() and not path.name.startswith(".")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        retained = 0
        for release in releases:
            if release.name in protected or retained < surface.retain_releases:
                retained += 1
                continue
            await asyncio.to_thread(shutil.rmtree, release)


class SurfaceApplication:
    """ASGI view resolving the selected release for every request."""

    def __init__(self, service, surface, *, preview=False, security=None):
        self.service = service
        self.surface = surface
        self.preview = preview
        self.security = security

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await JSONResponse({"error": "unsupported"}, status_code=404)(
                scope, receive, send
            )
            return
        if self.preview:
            root = scope.get('root_path', '').rstrip('/')
            relative = scope.get('path', '')
            if root and relative.startswith(root):
                relative = relative[len(root):]
            if relative.startswith('/_open/'):
                request = Request(scope, receive)
                if not self.service.previews.allows_exchange(request):
                    await JSONResponse({'error': 'untrusted_preview_origin'}, status_code=403)(scope, receive, send)
                    return
                try:
                    cookie, target = self.service.previews.exchange(self.surface, relative[len('/_open/'):])
                except LookupError:
                    response = JSONResponse({'error': 'invalid_preview_ticket'}, status_code=401)
                else:
                    response = RedirectResponse(target, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
                    response.set_cookie(self.service.previews.cookie, cookie, httponly=True, samesite='strict', max_age=8 * 3600, secure=request.url.scheme == 'https')
                await response(scope, receive, send)
                return
        if self.preview and self.security is not None:
            request = Request(scope, receive)
            principal = self.security.authenticate(request)
            if asyncio.iscoroutine(principal):
                principal = await principal
            if principal is None:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
            allowed = self.security.authorize(
                principal, {"scope": "surfaces:preview"}, request
            )
            if asyncio.iscoroutine(allowed):
                allowed = await allowed
            if not allowed:
                await JSONResponse({"error": "forbidden"}, status_code=403)(
                    scope, receive, send
                )
                return
        routed_scope = dict(scope)
        if self.preview:
            root_path = scope.get("root_path", "").rstrip("/")
            request_path = scope.get("path", "")
            relative_path = (
                request_path[len(root_path):]
                if root_path and request_path.startswith(root_path)
                else request_path
            )
            parts = relative_path.lstrip("/").split("/", 1)
            release = parts[0]
            try:
                directory = self.service.preview_path(self.surface, release)
            except LookupError:
                await JSONResponse({"error": "not_found"}, status_code=404)(
                    scope, receive, send
                )
                return
            remainder = parts[1] if len(parts) > 1 else ""
            routed_scope["path"] = f"{root_path}/{remainder}"
        else:
            directory = self.service.active_path(self.surface)
            if directory is None:
                await JSONResponse({"error": "not_published"}, status_code=503)(
                    scope, receive, send
                )
                return
        if self.preview and (not remainder or remainder == 'index.html'):
            html = await asyncio.to_thread((directory / 'index.html').read_text, encoding='utf-8')
            marker = '<meta name="framework-client-role" content="preview">'
            html, inserted = re.subn(r'(<head\b[^>]*>)', lambda match: match[0] + marker, html, count=1, flags=re.IGNORECASE)
            if not inserted:
                html = marker + html
            await HTMLResponse(html, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})(scope, receive, send)
            return
        await StaticFiles(directory=directory, html=True)(
            routed_scope, receive, send
        )


__all__ = [
    "ClientSurface", "SurfaceApplication", "SurfaceRelease", "SurfaceService",
]
