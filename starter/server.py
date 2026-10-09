"""Assemble the worker and HTTP surface without importing the product app."""

from pathlib import Path

from the_framework import ApplicationSpec, BuildContext
from the_framework.server import (
    CanonicalAgentApi,
    application_server_extensions,
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
    definition = ApplicationSpec({**application, "security": security}).with_server_extensions(
        *application_server_extensions(
            runtime, event_handshake=security.handshake,
            application_handshake=security.handshake,
            agent_api=CanonicalAgentApi(runtime, session_projection="display"),
        ),
    )
    app = definition.build(BuildContext({
        "plugin_state_path": root / "plugins.json",
        "surface_root": root / "surfaces",
        "ui_root": ROOT / "ui/dist",
        "surface_notify": lambda payload: runtime.publish({**payload, "type": "interface_refresh_requested"}),
        "surface_progress": runtime.publish,
        "data_root": root,
        "system_restart": restart,
        "system_state_path": root / "system.json",
    }))
    return app
