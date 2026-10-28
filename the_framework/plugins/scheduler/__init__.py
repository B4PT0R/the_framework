from ...server.composition.declarations import Capability, Plugin, ServerExtension
from .agent import SchedulerConfig, SchedulerExtension

SCHEDULER = Plugin(
    name="scheduler", agent_extension=SchedulerExtension,
    provides_capabilities=(Capability(name="scheduler.timing", version=1),),
    server_extensions=ServerExtension(
        name="scheduler", scope="session",
        service_factory="the_framework.plugins.scheduler.server:build_service",
    ),
)

__all__ = ["SCHEDULER", "SchedulerConfig", "SchedulerExtension"]
