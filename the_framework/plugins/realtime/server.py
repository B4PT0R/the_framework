"""Neutral realtime HTTP surface and controller lifecycle."""

from ...server.composition.application import ServerExtension
from .api import RealtimeApi
from .controller import RealtimeController


def build_server_extensions(context):
    client_factory = context.get("realtime_client_factory")
    return ServerExtension(
        name="realtime",
        service_factory=lambda runtime: RealtimeApi(
            runtime, RealtimeController(runtime, client_factory=client_factory),
        ),
        endpoints="service",
        stop=lambda service, _context: service.controller.stop(),
    )
