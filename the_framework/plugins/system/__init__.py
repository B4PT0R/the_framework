from ...server.composition.application import Plugin, ServerExtension, Capability
from .agent import SystemExtension

SYSTEM = Plugin(
    name="system", agent_extension=SystemExtension,
    server_extensions=ServerExtension(
        name="system", scope="session",
        service_factory="the_framework.plugins.system.server:build_service",
    ),
    provides_capabilities=(Capability(name="system.control", version=1),),
)

__all__ = ["SYSTEM", "SystemExtension"]
