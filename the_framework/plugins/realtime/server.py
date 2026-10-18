"""Neutral realtime HTTP surface and controller lifecycle."""

from ...server.composition.application import ServerExtension
from .api import RealtimeApi
from .controller import RealtimeController
from .context import CONTEXT_FACTORY


def build_server_extensions(context):
    async def update_binding(name, enabled, *, call=None):
        return await context.services["realtime"].update_plugin_binding(name, enabled, call=call)

    context.contribute("plugins.binding_update", update_binding)

    return ServerExtension(
        name="realtime", scope="session", service_factory=build_service,
        stop=lambda service, _context: service.stop(),
    )


def build_service(context, runtime):
    controller = RealtimeController(
        runtime, client_factory=context.plugin("realtime").get("client_factory"),
        context_factories=context.contributions(CONTEXT_FACTORY),
    )
    api = RealtimeApi(runtime, controller)
    runtime.register_reset_hooks(suspend=api.stop)
    return api
