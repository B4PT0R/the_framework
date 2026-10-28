"""Tools and live context for an agent's own persistent working process."""

from ...server.composition.declarations import Plugin, ServerExtension, CapabilityRequirement
from .agent import AgenticConfig, AgenticExtension

AGENTIC = Plugin(
    name="agentic", agent_extension=AgenticExtension,
    server_extensions=ServerExtension(
        name="agentic", scope="session",
        service_factory="the_framework.plugins.agentic.server:build_service",
        optional_services=("system",),
    ),
    optional_capabilities=(CapabilityRequirement(name="system.control", min_version=1),),
)

__all__ = ["AGENTIC", "AgenticConfig", "AgenticExtension"]
