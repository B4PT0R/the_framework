from ...server.composition.application import Plugin, Capability
from .agent import SystemExtension

SYSTEM = Plugin(
    name="system", agent_extension=SystemExtension,
    server_extension="the_framework.plugins.system.server:build_server_extensions",
    provides_capabilities=(Capability(name="system.control", version=1),),
)

__all__ = ["SYSTEM", "SystemExtension"]
