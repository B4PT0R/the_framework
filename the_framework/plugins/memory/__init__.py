"""Complete memory feature and its advanced agent customization surface."""

from ...server.composition.application import Plugin
from .agent import MemoryConfig, MemoryExtension, memory_path, message_text

MEMORY = Plugin(name="memory", agent_extension=MemoryExtension,
                server_extension="the_framework.plugins.memory.live:build_server_extensions")

__all__ = ["MEMORY", "MemoryConfig", "MemoryExtension", "memory_path", "message_text"]
