"""Durable system controls, with restart policy supplied by the application."""

from pathlib import Path

from ...server.composition.application import ServerExtension
from .service import SystemControlService


def build_server_extensions(context):
    path = context.plugin("system").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "system-control.json"

    def build(runtime):
        restart = runtime.restart_server if getattr(runtime, "restart", None) is not None else None
        system = SystemControlService(path, runtime.supervisor.send, restart=restart)
        runtime.bridge.register("system", system.handle, contextual=True)
        runtime.register_observer(system.observe)
        runtime.register_reset_hooks(resume=system.start)
        return system

    return ServerExtension(
        name="system", scope="session", service_factory=build,
    )
