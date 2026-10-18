"""Durable system controls, with restart policy supplied by the application."""

from pathlib import Path

from .service import SystemControlService


def build_service(context, runtime, realtime=None):
    path = context.plugin("system").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "system-control.json"
    restart = runtime.restart_server if getattr(runtime, "restart", None) is not None else None
    system = SystemControlService(
        path, runtime.supervisor.send, restart=restart,
        resume_realtime=realtime.controller.queue_restart_resume if realtime is not None else None,
    )
    if realtime is not None:
        realtime.controller.bind_restart_resume = system.bind_realtime_resume
    runtime.bridge.register("system", system.handle, contextual=True)
    runtime.register_observer(system.observe)
    runtime.register_reset_hooks(resume=system.start)
    return system
