"""Resolve an explicit execution target without guessing or falling back."""

import json
import os
from pathlib import Path
from typing import Literal

from .process import LocalProcess, RemoteProcess


ExecutionTarget = Literal["local", "host"]


def launch_process(target: ExecutionTarget, name: str, recipe):
    """Local means this backend environment; host requires runner credentials.

    Only the local recipe is executable here. For host execution the name selects
    a separately installed host recipe. Secrets stay in runner settings, never
    in session/plugin configuration. Validation cannot reach host capabilities.
    """
    if target == "local":
        return LocalProcess(recipe)
    if target != "host":
        raise ValueError("execution target must be 'local' or 'host'")
    from ..deployment.host import HostEndpoint

    path = os.environ.get("THE_FRAMEWORK_HOST_SETTINGS")
    if not path:
        raise RuntimeError("host execution is not configured")
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    if settings.get("validation") or not settings.get("host"):
        raise RuntimeError("host execution is unavailable in this runtime")
    return RemoteProcess(HostEndpoint(**settings["host"]), name)
