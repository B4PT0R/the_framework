"""Canonical socket connectivity is distinct from runtime subscribers."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.api.transport import CanonicalTransportSockets
from the_framework.server.composition.runtime import _conversation_extensions
from the_framework.server.runtime.application import ApplicationRuntime


class Socket:
    def __init__(self, *, accept_error=False):
        self.accept_error = accept_error
        self.accepted = asyncio.Event()
        self.incoming = asyncio.Queue()

    async def accept(self, **_options):
        if self.accept_error:
            raise RuntimeError("accept failed")
        self.accepted.set()

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, _output):
        raise RuntimeError("send failed")


def transport(runtime):
    return CanonicalTransportSockets(runtime, event_handshake=lambda _: None,
                                     application_handshake=lambda _: None)


def test_composition_exposes_transport_as_a_service():
    runtime = ApplicationRuntime(SimpleNamespace())
    extensions = _conversation_extensions(
        runtime, event_handshake=lambda _: None, application_handshake=lambda _: None,
    )
    sockets = next(extension for extension in extensions if extension.name == "canonical_transport")
    assert isinstance(sockets.service, CanonicalTransportSockets)
    assert sockets.service.runtime is runtime
    assert sockets.service.event_connection_count == 0


def test_multiple_sockets_release_presence_on_disconnect_and_cancellation():
    async def run():
        runtime = ApplicationRuntime(SimpleNamespace())
        sockets = transport(runtime)
        internal = runtime.stream()
        waiting = asyncio.create_task(anext(internal))
        await asyncio.sleep(0)
        assert runtime.subscribers and sockets.event_connection_count == 0
        first, second = Socket(), Socket()
        tasks = [asyncio.create_task(sockets.serve_events(socket, {"subprotocol": "events"}))
                 for socket in (first, second)]
        await first.accepted.wait()
        await second.accepted.wait()
        assert sockets.event_connection_count == 2
        await first.incoming.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(tasks[0], 1)
        assert sockets.event_connection_count == 1
        tasks[1].cancel()
        await asyncio.gather(tasks[1], return_exceptions=True)
        assert sockets.event_connection_count == 0
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        await internal.aclose()
        assert not runtime.subscribers

    asyncio.run(run())


def test_accept_and_send_failure_do_not_leave_presence():
    async def run():
        runtime = ApplicationRuntime(SimpleNamespace())
        sockets = transport(runtime)
        with pytest.raises(RuntimeError, match="accept failed"):
            await sockets.serve_events(Socket(accept_error=True), {"subprotocol": "events"})
        assert sockets.event_connection_count == 0
        socket = Socket()
        task = asyncio.create_task(sockets.serve_events(socket, {"subprotocol": "events"}))
        await socket.accepted.wait()
        while not runtime.subscribers:
            await asyncio.sleep(0)
        await runtime.publish({"type": "test"})
        with pytest.raises(RuntimeError, match="send failed"):
            await asyncio.wait_for(task, 1)
        assert sockets.event_connection_count == 0
        assert not runtime.subscribers

    asyncio.run(run())
