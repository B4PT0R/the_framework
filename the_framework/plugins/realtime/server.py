"""Neutral realtime HTTP surface and controller lifecycle."""

from ...server.composition.application import ServerExtension
from .api import RealtimeApi
from .controller import RealtimeController
from .context import CONTEXT_FACTORY


def build_server_extensions(context):
    client_factory = context.get("realtime_client_factory")

    def build(runtime):
        controller = RealtimeController(
            runtime, client_factory=client_factory,
            context_factories=context.contributions(CONTEXT_FACTORY),
        )
        api = RealtimeApi(runtime, controller)
        if context.get("plugin_binding_update") is None:
            context.provide("plugin_binding_update", api.update_plugin_binding)
        runtime.register_reset_hooks(suspend=api.stop)
        return api

    return ServerExtension(
        name="realtime", service_factory=build,
        endpoints="service",
        stop=lambda service, _context: service.stop(),
    )
