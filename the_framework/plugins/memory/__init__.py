"""Complete memory feature and its advanced agent customization surface."""

from ...server.composition.application import Plugin, ServerExtension
from .agent import MemoryConfig, MemoryExtension

MEMORY = Plugin(name="memory", agent_extension=MemoryExtension,
                configure="the_framework.plugins.memory.live:configure",
                server_extensions=ServerExtension(
                    name="memory", scope="session",
                    service_factory="the_framework.plugins.memory.server:build_service",
                ))

__all__ = ["MEMORY", "MemoryConfig", "MemoryExtension"]
