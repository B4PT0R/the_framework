"""Host-owned validation and release selection for immutable backends."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path

from ..utils.persistence import atomic_text_writer
from .image import DeploymentError, Docker, image_id

VALIDATION_VERSION = 2


def application_options(options):
    if options is None:
        options = {}
    if not isinstance(options, Mapping):
        raise TypeError("application options must be a mapping")
    if any(not isinstance(key, str) for key in options):
        raise ValueError("application option names must be strings")
    options = json.loads(json.dumps(dict(options), allow_nan=False, sort_keys=True))
    reserved = {"data_root", "token", "browser_token", "origin", "restart", "validation", "host"}
    if not isinstance(options, dict) or reserved.intersection(options):
        raise ValueError("application options cannot override core factory inputs")
    return options


class Deployment:
    """One local backend's durable release state, owned outside its container.

    Construction is inert. Building/testing never starts a production backend;
    selecting records an explicit next image and requires successful validation.
    """

    def __init__(self, root, *, docker=None):
        self.root = Path(root).expanduser().resolve()
        if "," in str(self.root):
            raise ValueError("deployment root cannot contain commas (Docker bind mount syntax)")
        self.docker = docker or Docker()

    def status(self):
        try:
            result = json.loads((self.root / "releases.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            result = {"version": 1, "current": None, "previous": None, "selected": None, "validated": {}}
        if result.get("version") != 1:
            raise DeploymentError("unsupported release state version")
        return result

    def _save(self, state):
        with atomic_text_writer(self.root / "releases.json") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")

    @contextmanager
    def _state_lock(self):
        from filelock import FileLock, Timeout

        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / "releases.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as error:
            raise DeploymentError("release state is busy") from error
        try:
            yield
        finally:
            lock.release()

    def serve(self, runtime, *, stop=None, timeout=90):
        """Run the external supervisor in the foreground; never inside an image.

        ``stop`` is an optional threading.Event for embedding/tests. A selected
        image is promoted only after readiness. Code rollback never undoes data.
        """
        from .supervisor import serve

        return serve(self, runtime, stop=stop, timeout=timeout)

    def _validated(self, state, identifier, options=None):
        proof = state["validated"].get(identifier, {})
        return (proof.get("version") == VALIDATION_VERSION
                and (options is None or proof.get("options") == application_options(options)))

    def select(self, identifier):
        identifier = image_id(identifier)
        with self._state_lock():
            state = self.status()
            if not self._validated(state, identifier):
                raise DeploymentError("test this exact image successfully before selecting it")
            self.docker.run("image", "inspect", identifier)
            state["selected"] = identifier
            self._save(state)
        return identifier

    def test(self, identifier, *, session=None, options=None, timeout=90):
        """Probe a candidate with no network, host data, journals or model turn.

        Only the explicitly supplied canonical JSON file is copied. The factory
        must honor validation=True (Framework runtime blocks generation too).
        All candidate files live on container tmpfs; only a temporary settings
        file is mounted read-only. A failure invalidates older validation proof.
        """
        from filelock import FileLock, Timeout

        identifier = image_id(identifier)
        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / f"test-{identifier[7:]}.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as error:
            raise DeploymentError("this image is already being tested") from error
        try:
            return self._test(identifier, session=session, options=options, timeout=timeout)
        finally:
            lock.release()

    def _test(self, identifier, *, session, options, timeout):
        identifier = image_id(identifier)
        options = application_options(options)
        if not 1 <= timeout <= 900:
            raise ValueError("validation timeout must be between 1 and 900 seconds")
        saved = None
        if session is not None:
            saved = json.loads(Path(session).read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not isinstance(saved.get("id"), str):
                raise ValueError("candidate session must be a canonical JSON object with an id")
        with self._state_lock():
            state = self.status()
            state["validated"].pop(identifier, None)
            if state["selected"] == identifier:
                state["selected"] = None
            self._save(state)
        name = "framework-test-" + uuid.uuid4().hex
        settings = {
            "token": secrets.token_urlsafe(32), "browser_token": secrets.token_urlsafe(32),
            "origin": "http://127.0.0.1:8080", "validation": True,
            "session": saved, "session_id": saved.get("id") if saved else None,
            "options": options,
        }
        created = False
        error = None
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="backend-test-") as directory:
            config = Path(directory) / "settings.json"
            config.write_text(json.dumps(settings), encoding="utf-8")
            # The enclosing temporary directory stays private (0700). The
            # mounted file must also be readable by a remapped container UID.
            os.chmod(config, 0o644)
            try:
                self.docker.run(
                    "create", "--name", name, "--network", "none", "--read-only",
                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--memory", "1g", "--cpus", "2", "--pids-limit", "256",
                    "--tmpfs", "/tmp:rw,nosuid,size=256m", "--tmpfs", "/data:rw,nosuid,size=256m",
                    "--mount", f"type=bind,source={config},target=/run/backend/settings.json,readonly",
                    identifier, timeout=min(30, timeout),
                )
                created = True
                self.docker.run("start", name, timeout=min(15, timeout))
                self._wait_ready(name, timeout=max(0, timeout - (time.monotonic() - started)), complete=True)
            except BaseException as failure:
                error = failure
                if isinstance(failure, Exception) and created:
                    try:
                        logs = self.docker.run("logs", "--tail", "60", name, timeout=5)
                    except DeploymentError:
                        logs = "(logs unavailable)"
                    details = f"candidate validation failed: {failure}\n{logs}"
                    for secret in (settings["token"], settings["browser_token"]):
                        details = details.replace(secret, "[redacted]")
                    error = DeploymentError(details)
            finally:
                # Remove by our random name even if create returned ambiguously
                # (client timeout). Never touch another instance's containers.
                try:
                    self.docker.remove(name)
                except DeploymentError:
                    raise DeploymentError(f"candidate cleanup failed; remove {name} manually") from error
            if error is not None:
                raise error
        with self._state_lock():
            state = self.status()
            state["validated"][identifier] = {"version": VALIDATION_VERSION, "at": time.time(), "options": options}
            self._save(state)
        return {"image": identifier, "status": "ok"}

    def _wait_ready(self, name, *, timeout, complete=False):
        deadline = time.monotonic() + timeout
        last_error = "no probe completed"
        while time.monotonic() < deadline:
            info = self.docker.inspect(name, timeout=max(0.1, min(10, deadline - time.monotonic())))
            if not info["State"]["Running"]:
                raise DeploymentError(f"backend exited before readiness (code {info['State']['ExitCode']})")
            try:
                self.docker.run(
                    "exec", name, "python", "-m", "the_framework.deployment.runner", "probe",
                    *(("--complete",) if complete else ()),
                    timeout=max(0.1, min(10, deadline - time.monotonic())),
                )
                return
            except DeploymentError as error:
                last_error = str(error)
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        raise DeploymentError(f"backend readiness timed out: {last_error}")
