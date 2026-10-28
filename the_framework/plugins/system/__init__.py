from ...server.composition.declarations import Plugin, ServerExtension, Capability, CapabilityRequirement
from .agent import SystemExtension

SYSTEM = Plugin(
    name="system", agent_extension=SystemExtension,
    server_extensions=ServerExtension(
        name="system", scope="session",
        service_factory="the_framework.plugins.system.server:build_service",
        optional_services=("realtime",),
    ),
    provides_capabilities=(Capability(name="system.control", version=1),),
    optional_capabilities=(CapabilityRequirement(name="realtime.session", min_version=1),),
)

__all__ = ["SYSTEM", "SystemExtension"]
