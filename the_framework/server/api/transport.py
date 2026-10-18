"""Canonical event and application-bridge WebSocket transports."""

from .websockets import WebSocketEndpoint, serve_event_stream_until_disconnect


class CanonicalTransportSockets:
    """Expose one runtime's ordered events and application capability bridge."""

    def __init__(self, runtime, *, event_handshake, application_handshake):
        self.runtime = runtime
        self._event_connections = 0
        self._sockets = set()
        self._primary_socket = None
        self.primary_event_connection_count = 0
        self.events_socket = WebSocketEndpoint(
            path="/api/v1/events",
            handler=self.serve_events,
            name="canonical_events",
            handshake=event_handshake,
        )
        self.application_socket = WebSocketEndpoint(
            path="/api/v1/application",
            handler=self.serve_application,
            name="canonical_application",
            handshake=application_handshake,
        )

    @property
    def event_connection_count(self) -> int:
        """Accepted canonical event sockets, not internal or paired subscribers.

        This is transport connectivity, not window visibility or user attention.
        """
        return self._event_connections

    async def serve_events(self, socket, connection):
        role = connection.get('client_role', 'primary')
        if role not in {'primary', 'preview'}:
            await socket.close(code=1008)
            return
        primary = role == 'primary'
        if primary and self._primary_socket is not None:
            await socket.close(code=1013)
            return
        if primary:
            self._primary_socket = socket
        accepted = False
        try:
            await socket.accept(subprotocol=connection["subprotocol"])
            accepted = True
            self._sockets.add(socket)
            self._event_connections += 1
            if primary:
                self.primary_event_connection_count += 1
            async def send_events():
                async for output in self.runtime.stream():
                    await socket.send_json(output)

            await serve_event_stream_until_disconnect(socket, send_events)
        finally:
            if accepted:
                self._sockets.discard(socket)
                self._event_connections -= 1
                if primary:
                    self.primary_event_connection_count -= 1
            if self._primary_socket is socket:
                self._primary_socket = None

    async def stop(self):
        """Close this session's event connections on lifecycle shutdown."""
        for socket in tuple(self._sockets):
            await socket.close(code=1012)

    async def serve_application(self, socket, connection):
        if connection.get('client_role', 'primary') != 'primary':
            await socket.close(code=1008)
            return
        await self.runtime.bridge.serve(
            socket, subprotocol=connection["subprotocol"],
        )


__all__ = ["CanonicalTransportSockets"]
