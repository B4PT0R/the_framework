"""Neutral realtime HTTP surface and controller lifecycle."""

from .api import RealtimeApi
from .controller import RealtimeController
from .context import CONTEXT_FACTORY


def configure(context):
    async def update_binding(name, enabled, *, call=None):
        return await context.services["realtime"].update_plugin_binding(name, enabled, call=call)

    context.contribute("plugins.binding_update", update_binding)


def build_service(context, runtime, canonical_transport):
    controller = RealtimeController(
        runtime, client_factory=context.plugin("realtime").get("client_factory"),
        context_factories=context.contributions(CONTEXT_FACTORY),
    )
    api = RealtimeApi(runtime, controller, canonical_transport)
    runtime.bridge.register("realtime", api.handle, contextual=True)
    runtime.register_observer(api.observe)
    runtime.register_reset_hooks(suspend=api.shutdown)
    runtime.register_restart_hook(api.prepare_restart)
    return api
