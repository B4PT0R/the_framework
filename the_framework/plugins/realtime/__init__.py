from ...server.composition.application import Plugin, Capability, ServerExtension
from .agent import RealtimeConfig, RealtimeExtension

REALTIME = Plugin(
    name="realtime", agent_extension=RealtimeExtension,
    server_extensions=ServerExtension(
        name="realtime", scope="session",
        service_factory="the_framework.plugins.realtime.server:build_service",
        stop=lambda service, _context: service.stop(),
    ),
    configure="the_framework.plugins.realtime.server:configure",
    provides_capabilities=(Capability(name="realtime.session", version=1),),
)

__all__ = ["REALTIME", "RealtimeConfig", "RealtimeExtension"]
