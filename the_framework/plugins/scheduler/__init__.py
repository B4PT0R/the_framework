from ...server.composition.application import Plugin, ServerExtension
from .agent import SchedulerConfig, SchedulerExtension

SCHEDULER = Plugin(
    name="scheduler", agent_extension=SchedulerExtension,
    server_extensions=ServerExtension(
        name="scheduler", scope="session",
        service_factory="the_framework.plugins.scheduler.server:build_service",
    ),
)

__all__ = ["SCHEDULER", "SchedulerConfig", "SchedulerExtension"]
