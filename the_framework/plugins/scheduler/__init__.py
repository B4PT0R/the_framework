from ...server.composition.application import Plugin
from .agent import SchedulerConfig, SchedulerExtension

SCHEDULER = Plugin(
    name="scheduler", agent_extension=SchedulerExtension,
    server_extension="the_framework.plugins.scheduler.server:build_server_extensions",
)

__all__ = ["SCHEDULER", "SchedulerConfig", "SchedulerExtension"]
