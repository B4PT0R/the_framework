"""Assemble the worker and HTTP surface without importing the product app."""

import asyncio
from pathlib import Path

from the_framework import ApplicationSpec, BuildContext, ServerExtension
from the_framework.server import (
    ApplicationHealthApi,
    CanonicalAgentApi,
    CanonicalTransportSockets,
    WorkerSupervisor,
)
from the_framework.server.runtime.application import ApplicationRuntime

from .application import ROOT, application
from .security import LocalSecurity

REFERENCE = "starter.application:application"


def create_app(data_root, *, token, origin, runtime=None, restart=None):
    root = Path(data_root).resolve()
    if runtime is None:
        runtime = ApplicationRuntime(WorkerSupervisor(
            root / "session.json", application_reference=REFERENCE,
            agent_name=application.primary_agent.name,
            plugin_state_path=root / "plugins.json",
        ))
    security = LocalSecurity(token, origin=origin)
    sockets = CanonicalTransportSockets(runtime, event_handshake=security.handshake,
                                        application_handshake=security.handshake)
    app = None
    text_mode_lock = asyncio.Lock()

    def voice_service():
        if app is None:
            raise RuntimeError("voice service is unavailable before application build")
        return app.state.application.services.get("voice")

    async def binding(name, enabled, *, call=None):
        voice = voice_service()
        async with (voice.lock if voice is not None else text_mode_lock):
            if name == "realtime" and not enabled and voice is not None:
                await voice.controller.stop()
            await runtime.set_plugin_binding(name, enabled, call=call)

    definition = ApplicationSpec({**application, "security": security}).with_server_extensions(
        ServerExtension(name="runtime", service=runtime),
        ServerExtension(name="conversation", requires_services=("runtime",), endpoints=(
            CanonicalAgentApi(runtime, session_projection="display"),
            ApplicationHealthApi(),
        ), websockets=(sockets.events_socket, sockets.application_socket)),
    )
    app = definition.build(BuildContext({
        "plugin_state_path": root / "plugins.json",
        "plugin_binding_update": binding,
        "surface_root": root / "surfaces",
        "ui_root": ROOT / "ui/dist",
        "surface_notify": lambda payload: runtime.publish({**payload, "type": "interface_refresh_requested"}),
        "surface_progress": runtime.publish,
        "data_root": root,
        "text_mode_lock": text_mode_lock,
        "restart": restart,
    }))
    return app
