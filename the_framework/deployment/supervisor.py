"""Host-only image replacement and crash recovery. Never imported by the runner."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from filelock import FileLock, Timeout

from ..utils.persistence import atomic_text_writer
from .image import DeploymentError, image_id
from .deployment import application_options


@dataclass(frozen=True)
class BackendRuntime:
    """Explicit production inputs, stored privately outside the immutable image.

    Only loopback HTTP is published. ``environment`` is installed by the runner,
    not passed as inspectable Docker environment arguments. ``options`` supplies
    application-specific factory inputs; core factory parameters are reserved.
    The data directory is Deployment.root/data and is NEVER rolled back.
    """

    token: str = field(repr=False)
    browser_token: str = field(repr=False)
    host: object = field(default=None, repr=False)
    workspaces: dict[str, Path] = field(default_factory=dict)
    mounts: dict[str, dict] = field(default_factory=dict)
    port: int = 8765
    origin: str | None = None
    user: int = field(default_factory=lambda: getattr(os, "getuid", lambda: 1000)())
    environment: dict[str, str] = field(default_factory=dict, repr=False)
    options: dict = field(default_factory=dict)

    def __post_init__(self):
        if not all(isinstance(token, str) and token for token in (self.token, self.browser_token)):
            raise ValueError("backend and browser tokens must be nonempty strings")
        if type(self.port) is not int or type(self.user) is not int:
            raise TypeError("backend port and user must be integers")
        for name in ("workspaces", "mounts", "environment"):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"{name} must be a mapping")
        if self.origin is not None and not isinstance(self.origin, str):
            raise TypeError("origin must be a string")
        if self.host is not None:
            from .host import HostEndpoint

            if not isinstance(self.host, HostEndpoint):
                raise TypeError("host must be a HostEndpoint")
            if {self.token, self.browser_token}.intersection({self.host.token, self.host.browser_token, self.host.execution_token}):
                raise ValueError("host tokens must be distinct from backend tokens")
        if not self.token or not self.browser_token or self.token == self.browser_token:
            raise ValueError("distinct nonempty backend and browser tokens are required")
        if not 1 <= self.port <= 65535 or self.user < 0:
            raise ValueError("invalid backend port or user")
        if self.origin is not None:
            from urllib.parse import urlsplit

            try:
                parsed = urlsplit(self.origin)
                port = parsed.port  # urlsplit alone accepts malformed/out-of-range ports.
                valid = (
                    parsed.scheme in ("http", "https") and parsed.hostname
                    and not (parsed.path or parsed.query or parsed.fragment)
                    and parsed.username is None and parsed.password is None
                    and not parsed.netloc.endswith(":") and port != 0
                    and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.origin)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("origin must be an HTTP origin with a valid authority, without path or credentials")
        if any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name) for name in self.workspaces):
            raise ValueError("workspace names must be simple lowercase identifiers")
        workspaces = {name: Path(path).expanduser().resolve() for name, path in self.workspaces.items()}
        if any(not path.is_dir() or "," in str(path) for path in workspaces.values()):
            raise ValueError("workspaces must be explicit existing directories without commas")
        object.__setattr__(self, "workspaces", workspaces)
        mounts = {}
        reserved = {"bin", "sbin", "usr", "etc", "opt", "run", "proc", "sys", "dev", "tmp", "data", "workspaces"}
        for target, spec in self.mounts.items():
            path = PurePosixPath(target)
            if (not path.is_absolute() or str(path) != target or ".." in path.parts
                    or len(path.parts) < 2 or path.parts[1] in reserved or "," in target):
                raise ValueError("mount targets must be normalized absolute application paths")
            if not isinstance(spec, dict) or set(spec) - {"source", "readonly"} or "source" not in spec:
                raise ValueError("mounts require source and optional readonly")
            source = Path(spec["source"]).expanduser().resolve()
            readonly = spec.get("readonly", True)
            if not source.exists() or "," in str(source) or type(readonly) is not bool:
                raise ValueError("mount source must exist and readonly must be a boolean")
            if any(path in PurePosixPath(other).parents or PurePosixPath(other) in path.parents for other in mounts):
                raise ValueError("mount targets must not overlap")
            mounts[target] = {"source": source, "readonly": readonly}
        object.__setattr__(self, "mounts", mounts)
        object.__setattr__(self, "options", application_options(self.options))
        if any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v or not k or "=" in k for k, v in self.environment.items()):
            raise ValueError("environment must contain valid string keys and values")
        object.__setattr__(self, "environment", dict(self.environment))

    def settings(self):
        return {
            "token": self.token, "browser_token": self.browser_token,
            "origin": self.origin or f"http://127.0.0.1:{self.port}",
            "environment": self.environment, "options": self.options,
            "validation": False,
            "host": self.host.settings() if self.host is not None else None,
        }


def serve(deployment, runtime: BackendRuntime, *, stop=None, timeout=90):
    if not isinstance(runtime, BackendRuntime):
        raise TypeError("serve requires BackendRuntime inputs")
    if not 1 <= timeout <= 900:
        raise ValueError("readiness timeout must be between 1 and 900 seconds")
    runtime = replace(runtime)  # Snapshot and revalidate mutable input mappings.
    stop = stop if stop is not None else threading.Event()
    root = deployment.root
    root.mkdir(parents=True, exist_ok=True)
    lease = FileLock(root / "supervisor.lock", timeout=0)
    try:
        lease.acquire()
    except Timeout as error:
        raise DeploymentError("a supervisor already owns this deployment") from error
    supervisor = _Supervisor(deployment, runtime, timeout)
    try:
        if not supervisor.recover():
            supervisor.replace()
        while not stop.wait(0.2):
            info = deployment.docker.inspect(supervisor.name)["State"]
            if info["Running"]:
                continue
            if info["ExitCode"] != 75:
                raise DeploymentError(f"backend exited unexpectedly ({info['ExitCode']}); explicit host recovery required")
            # The application drained the completed turn before exiting 75.
            supervisor.replace()
        supervisor.stop()
    finally:
        lease.release()


class _Supervisor:
    def __init__(self, deployment, runtime, timeout):
        self.deployment, self.runtime, self.timeout = deployment, runtime, timeout
        self.docker = deployment.docker
        self.name = "framework-backend-" + hashlib.sha256(str(deployment.root).encode()).hexdigest()[:20]
        self.config = deployment.root / "settings.json"

    def configure(self):
        root = self.deployment.root
        (root / "data").mkdir(exist_ok=True)
        # The host owns this directory; container runs with its numeric UID.
        # No world-readable production secrets, even momentarily.
        with atomic_text_writer(self.config) as stream:
            json.dump(self.runtime.settings(), stream)

    def stop(self, *, drain=True):
        # Query by name with `ps`: missing and daemon failure are distinct.
        if not self.docker.run("ps", "--all", "--filter", f"name=^/{self.name}$", "--format", "{{.ID}}"):
            return
        if self.docker.inspect(self.name)["State"]["Running"]:
            # Failure to drain is not permission to kill a still-running turn.
            if drain:
                self.docker.run("exec", self.name, "python", "-m", "the_framework.deployment.runner", "drain", timeout=150)
            self.docker.run("stop", "--time", "30", self.name, timeout=40)
        self.docker.remove(self.name)

    def start(self, identifier, *, options=None):
        identifier = image_id(identifier)
        if options is not None:
            self.runtime = replace(self.runtime, options=options)
        self.configure()
        self.docker.run(
            "create", "--name", self.name, "--read-only", "--user", str(self.runtime.user),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "512", "--init", "--tmpfs", "/tmp:rw,nosuid,size=512m",
            "--publish", f"127.0.0.1:{self.runtime.port}:8080",
            "--mount", f"type=bind,source={self.config},target=/run/backend/settings.json,readonly",
            "--mount", f"type=bind,source={self.deployment.root / 'data'},target=/data",
            *(argument for name, path in self.runtime.workspaces.items()
              for argument in ("--mount", f"type=bind,source={path},target=/workspaces/{name}")),
            *(argument for target, spec in self.runtime.mounts.items()
              for argument in ("--mount", f"type=bind,source={spec['source']},target={target}" + (",readonly" if spec["readonly"] else ""))),
            identifier,
        )
        self.docker.run("start", self.name)
        self.deployment._wait_ready(self.name, timeout=self.timeout)

    def recover(self):
        state = self.deployment.status()
        pending = state.get("pending")
        if not pending:
            return
        # Any interrupted transition is uncommitted. Do not guess that a
        # partially started candidate was healthy: restore the known-good ID.
        self.stop(drain=pending["phase"] == "stopping")
        previous = pending["rollback"]
        if previous:
            self.start(previous, options=state.get("current_options", {}))
        with self.deployment._state_lock():
            state = self.deployment.status()
            if state["selected"] == pending["image"]:
                state["selected"] = None
            state.update(pending=None, error="interrupted promotion recovered to the last healthy image")
            self.deployment._save(state)
        if previous is None:
            raise DeploymentError("interrupted first launch; test/select an image explicitly before serving again")
        return True

    def replace(self):
        with self.deployment._state_lock():
            state = self.deployment.status()
            target = state["selected"] or state["current"]
            if target is None:
                raise DeploymentError("no tested image has been selected")
            if not self.deployment._validated(state, target, self.runtime.options):
                raise DeploymentError("image no longer has valid test proof for these application options")
            proof = state["validated"][target].copy()
            previous = state["current"]
            previous_options = state.get("current_options", {})
            state["pending"] = {"image": target, "rollback": previous, "phase": "stopping"}
            self.deployment._save(state)
        starting = False
        try:
            self.stop()
            with self.deployment._state_lock():
                state = self.deployment.status()
                state["pending"]["phase"] = "starting"
                self.deployment._save(state)
            starting = True
            self.start(target)
            with self.deployment._state_lock():
                state = self.deployment.status()
                if state["validated"].get(target) != proof:
                    raise DeploymentError("test proof invalidated during startup")
                if target != previous:
                    state["previous"] = previous
                if state["selected"] == target:
                    state["selected"] = None
                state.update(current=target, current_options=self.runtime.options, pending=None, error=None)
                self.deployment._save(state)
        except Exception as failure:
            # Keep pending until the recovery itself has succeeded. If Docker
            # is unavailable, a future host start resumes that same recovery.
            if not starting:
                # The incumbent did not drain; never kill it or launch a rival.
                with self.deployment._state_lock():
                    state = self.deployment.status()
                    state.update(pending=None, error="incumbent could not be drained; replacement refused")
                    self.deployment._save(state)
                raise DeploymentError("incumbent could not be drained") from failure
            self.stop(drain=False)
            if previous:
                self.start(previous, options=previous_options)
            with self.deployment._state_lock():
                state = self.deployment.status()
                if state["selected"] == target:
                    state["selected"] = None
                state.update(pending=None, error=f"image startup failed ({type(failure).__name__}); previous image restored" if previous else "first image startup failed")
                self.deployment._save(state)
            if previous is None:
                raise DeploymentError("first image startup failed; no healthy rollback exists") from failure
            return
