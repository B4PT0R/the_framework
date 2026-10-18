"""Compose the persistent conversation with the starter's local access policy."""

from pathlib import Path

from the_framework import BuildContext

from .application import application
from .security import LocalSecurity

REFERENCE = "starter.application:application"


def create_app(data_root, *, token, origin, runtime=None, restart=None, browser_token=None):
    security = LocalSecurity(token, origin=origin, browser_token=browser_token)
    return application.build_conversation(
        REFERENCE,
        BuildContext(
            data_root=data_root, origin=origin, restart=restart,
            plugins={"system": {"state_path": Path(data_root) / "system.json"}},
        ),
        security=security, handshake=security.handshake,
        runtime_factory=(lambda _supervisor, _context: runtime) if runtime is not None else None,
        session_projection="display",
    )
