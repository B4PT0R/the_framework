"""Assemble the worker and HTTP surface without importing the product app."""

import asyncio
import sys
from pathlib import Path

from the_framework import AgentApplication, BuildContext, Extension
from the_framework.agent.runtime.protocol import PluginBindingRequest
from the_framework.utils.ids import timestamp_id
from the_framework.server import (
    ApplicationHealthApi,
    CanonicalAgentApi,
    CanonicalTransportSockets,
    WorkerSupervisor,
    bind_application_controls,
)
from the_framework.server.runtime.application import ApplicationRuntime
from the_framework.server.runtime.fleet import FleetSupervisor

from .application import ROOT, application
from .security import LocalSecurity

REFERENCE = "starter.application:application"


def create_app(data_root, *, token, origin, runtime=None, restart=None):
    root = Path(data_root).resolve()
    if runtime is None:
        supervisor = WorkerSupervisor(root / "session.json", command=[
            sys.executable, "-m", "the_framework.agent.runtime.worker_process",
            "--session", str(root / "session.json"),
            "--application", REFERENCE, "--agent", application.primary_agent.name,
        ])
        runtime = ApplicationRuntime(supervisor, fleet_factory=lambda path, profiles: FleetSupervisor(
            path, profiles, application_reference=REFERENCE,
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

    async def binding(name, enabled):
        voice = voice_service()
        async with (voice.lock if voice is not None else text_mode_lock):
            if name == "realtime" and not enabled and voice is not None:
                await voice.controller.stop()
            result = await runtime.command(PluginBindingRequest(
                id=timestamp_id(), plugin=name, enabled=enabled,
            ))
            if result is None or result.type != "agent.plugin.binding.updated":
                raise RuntimeError("worker did not confirm plugin activation")

    definition = AgentApplication({**application, "security": security}).with_extensions(
        Extension(name="runtime", service=runtime),
        Extension(name="conversation", requires=("runtime",), endpoints=(
            CanonicalAgentApi(runtime, session_projection="display"),
            ApplicationHealthApi(),
        ), websockets=(sockets.events_socket, sockets.application_socket)),
    )
    app = definition.build(BuildContext({
        "plugin_state_path": root / "plugins.json",
        "plugin_binding_update": binding,
        "surface_root": root / "surfaces",
        "surface_notify": lambda payload: runtime.publish({**payload, "type": "interface_refresh_requested"}),
        "surface_progress": runtime.publish,
        "data_root": root,
        "text_mode_lock": text_mode_lock,
        "restart": restart,
    }))
    bind_application_controls(runtime, app, ui_root=ROOT / "ui/dist")
    return app
