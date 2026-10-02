from ...server.composition.application import Plugin, Capability
from .agent import RealtimeConfig, RealtimeExtension

REALTIME = Plugin(
    name="realtime", agent_extension=RealtimeExtension,
    server_extension="the_framework.plugins.realtime.server:build_server_extensions",
    provides_capabilities=(Capability(name="realtime.session", version=1),),
)

__all__ = ["REALTIME", "RealtimeConfig", "RealtimeExtension"]
