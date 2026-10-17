"""The standard server surface, assembled through ordinary declarations."""

from collections.abc import Callable

from ..runtime.application import ApplicationRuntime
from ..api.agent import CanonicalAgentApi
from ..api.health import ApplicationHealthApi
from ..api.transport import CanonicalTransportSockets
from .application import ServerExtension


def _conversation_extensions(
    runtime: ApplicationRuntime, *, event_handshake: Callable,
    application_handshake: Callable, agent_api: CanonicalAgentApi | None = None,
    health_api: ApplicationHealthApi | None = None,
) -> tuple[ServerExtension, ...]:
    """Declare runtime, agent HTTP API, health, and sockets with explicit security.

    Pass custom APIs to preserve application projections. Each component remains
    independently composable when an application needs another server layout.
    """
    sockets = CanonicalTransportSockets(
        runtime, event_handshake=event_handshake,
        application_handshake=application_handshake,
    )
    return (
        ServerExtension(name="runtime", service=runtime, endpoints=(
            agent_api if agent_api is not None else CanonicalAgentApi(runtime),
        )),
        ServerExtension(name="health", endpoints=(
            health_api if health_api is not None else ApplicationHealthApi(runtime=runtime),
        ), requires_services=("runtime",)),
        ServerExtension(name="canonical_transport", service=sockets, websockets=(
            sockets.events_socket, sockets.application_socket,
        ), requires_services=("runtime",)),
    )
