"""Canonical event and application-bridge WebSocket transports."""

import asyncio

from starlette.websockets import WebSocketDisconnect

from .websockets import WebSocketEndpoint


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
            try:
                async for output in self.runtime.stream():
                    await socket.send_json(output)
            except asyncio.CancelledError:
                # This task is owned by this socket. Its cancellation is the
                # normal counterpart of the peer receiver finishing first.
                return

        async def wait_for_disconnect():
            try:
                while True:
                    message = await socket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
            except asyncio.CancelledError:
                return

        sender = asyncio.create_task(send_events())
        receiver = asyncio.create_task(wait_for_disconnect())
        tasks = (sender, receiver)
        done = set()
        try:
            try:
                done, _pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                # The ASGI connection owns this coroutine. Finish its local
                # cleanup before reporting the connection closed.
                done = set()
        finally:
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error and not isinstance(error, WebSocketDisconnect):
                raise error

    async def serve_application(self, socket, connection):
        await self.runtime.application.serve(
            socket, subprotocol=connection["subprotocol"],
        )


__all__ = ["CanonicalTransportSockets"]
