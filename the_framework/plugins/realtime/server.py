"""Neutral realtime HTTP surface and controller lifecycle."""

from ...server.composition.application import ServerExtension
from .api import RealtimeApi
from .controller import RealtimeController


def build_server_extensions(context):
    client_factory = context.get("realtime_client_factory")

    def build(runtime):
        api = RealtimeApi(runtime, RealtimeController(runtime, client_factory=client_factory))
        runtime.register_reset_hooks(suspend=api.stop)
        return api

    return ServerExtension(
        name="realtime", service_factory=build,
        endpoints="service",
        stop=lambda service, _context: service.stop(),
    )
