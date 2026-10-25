"""The standard server surface, assembled through ordinary declarations."""

from collections.abc import Callable
from pathlib import Path

from ..api.agent import CanonicalAgentApi
from ..api.bucket import FileBucket
from ..api.chat import ChatApi
from ..api.health import ApplicationHealthApi
from ..api.transport import CanonicalTransportSockets
from ..runtime.session import SessionRuntime
from .application import ServerExtension


def _conversation_extensions(
    runtime: SessionRuntime, *, data_root: str | Path, event_handshake: Callable,
    application_handshake: Callable, agent_api: CanonicalAgentApi | None = None,
    health_api: ApplicationHealthApi | None = None,
) -> tuple[ServerExtension, ...]:
    """Declare runtime, chat, agent HTTP API, health and authenticated sockets.

    Pass custom APIs to preserve application projections. Each component remains
    independently composable when an application needs another server layout.
    """
    sockets = CanonicalTransportSockets(
        runtime, event_handshake=event_handshake,
        application_handshake=application_handshake,
    )
    return (
        ServerExtension(name="runtime", service=runtime, scope="session", endpoints=(
            agent_api if agent_api is not None else CanonicalAgentApi(runtime),
        )),
        ServerExtension(
            name="chat", scope="session",
            service_factory=lambda runtime, canonical_transport, realtime=None: ChatApi(
                runtime, FileBucket(data_root).root, realtime, clients=canonical_transport.clients,
            ),
        ),
        ServerExtension(name="health", scope="session", endpoints=(
            health_api if health_api is not None else ApplicationHealthApi(runtime=runtime),
        ), requires_services=("runtime",)),
        ServerExtension(name="canonical_transport", scope="session", service=sockets, websockets=(
            sockets.events_socket, sockets.application_socket,
        ), requires_services=("runtime",)),
    )
