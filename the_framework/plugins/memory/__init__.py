"""Complete memory feature and its advanced agent customization surface."""

from ...server.composition.application import Plugin
from .agent import MemoryConfig, MemoryExtension

MEMORY = Plugin(name="memory", agent_extension=MemoryExtension,
                configure="the_framework.plugins.memory.live:configure")

__all__ = ["MEMORY", "MemoryConfig", "MemoryExtension"]
