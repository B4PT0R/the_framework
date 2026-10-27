"""Durable system controls, with restart policy supplied by the application."""

import asyncio
from pathlib import Path

from .service import SystemControlService


def build_service(context, runtime, realtime=None):
    path = context.plugin("system").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "system-control.json"
    host = None if context.get("validation", False) else context.plugin("system").get("host")
    if host is not None:
        async def deployment_control(method, payload):
            if method not in {"build", "test", "select", "status"}:
                raise ValueError("unsupported deployment operation")
            return await asyncio.to_thread(host.call, method, payload)

        runtime.bridge.register("deployment", deployment_control)
    restart = runtime.restart_server if getattr(runtime, "restart", None) is not None else None
    if context.get("validation", False):
        restart = None
    system = SystemControlService(
        path, runtime.supervisor.send, restart=restart,
        resume_realtime=realtime.controller.queue_restart_resume if realtime is not None else None,
        cancel_realtime_resume=realtime.controller.cancel_restart_resume if realtime is not None else None,
        realtime_call_id=(lambda: realtime.call_id if realtime.controller.active else None)
        if realtime is not None else None,
    )
    if realtime is not None:
        realtime.controller.bind_restart_resume = system.bind_realtime_resume
    runtime.bridge.register("system", system.handle, contextual=True)
    runtime.register_observer(system.observe)
    runtime.register_reset_hooks(suspend=system.stop, resume=system.start)
    return system
