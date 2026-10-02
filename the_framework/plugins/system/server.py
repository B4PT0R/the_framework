"""Durable system controls, with restart policy supplied by the application."""

from pathlib import Path

from ...server.composition.application import ServerExtension
from .service import SystemControlService


def build_server_extensions(context):
    path = context.get("system_state_path")
    if path is None:
        path = Path(context.require("data_root")) / "system-control.json"
    restart = context.get("system_restart")

    def build(runtime):
        system = SystemControlService(path, runtime.supervisor.send, restart=restart)
        runtime.application.register("system", system.handle, contextual=True)
        runtime.register_observer(system.observe)
        runtime.register_reset_hooks(resume=system.start)
        return system

    return ServerExtension(
        name="system", service_factory=build,
    )
