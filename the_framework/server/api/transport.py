"""Canonical event and application-bridge WebSocket transports."""

from .websockets import WebSocketEndpoint, serve_event_stream_until_disconnect


class CanonicalTransportSockets:
    """Expose one runtime's ordered events and application capability bridge."""

    def __init__(self, runtime, *, event_handshake, application_handshake):
        self.runtime = runtime
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

    async def serve_events(self, socket, connection):
        await socket.accept(subprotocol=connection["subprotocol"])

        async def send_events():
            async for output in self.runtime.stream():
                await socket.send_json(output)

        await serve_event_stream_until_disconnect(socket, send_events)

    async def serve_application(self, socket, connection):
        await self.runtime.application.serve(
            socket, subprotocol=connection["subprotocol"],
        )


__all__ = ["CanonicalTransportSockets"]
