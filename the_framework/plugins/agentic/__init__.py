"""Tools and live context for an agent's own persistent working process."""

from ...server.composition.application import Plugin, ServerExtension
from .agent import AgenticConfig, AgenticExtension

AGENTIC = Plugin(
    name="agentic", agent_extension=AgenticExtension,
    server_extensions=ServerExtension(
        name="agentic", scope="session",
        service_factory="the_framework.plugins.agentic.server:build_service",
    ),
)

__all__ = ["AGENTIC", "AgenticConfig", "AgenticExtension"]
