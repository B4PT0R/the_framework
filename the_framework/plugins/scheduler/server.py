"""The scheduler's complete server integration."""

from pathlib import Path

from .service import SchedulerService


def build_service(context, runtime):
    path = context.plugin("scheduler").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "scheduler.json"
    scheduler = SchedulerService(path, runtime.submit)
    runtime.bridge.register("scheduler", scheduler.handle)
    runtime.register_observer(scheduler.observe)
    runtime.register_reset_hooks(suspend=scheduler.stop, resume=scheduler.start)
    return scheduler
