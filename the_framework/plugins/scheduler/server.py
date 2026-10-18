"""The scheduler's complete server integration."""

from pathlib import Path

from ...server.composition.application import ServerExtension
from .service import SchedulerService


def build_server_extensions(context):
    path = context.plugin("scheduler").get("state_path")
    if path is None:
        path = Path(context.require("session_path")).parent / "scheduler.json"

    def build(runtime):
        scheduler = SchedulerService(path, runtime.submit)
        runtime.application.register("scheduler", scheduler.handle)
        runtime.register_observer(scheduler.observe)
        runtime.register_reset_hooks(suspend=scheduler.stop, resume=scheduler.start)
        return scheduler

    return ServerExtension(
        name="scheduler", scope="session", service_factory=build,
    )
