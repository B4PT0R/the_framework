"""Canonical socket connectivity is distinct from runtime subscribers."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.api.transport import CanonicalTransportSockets
from the_framework.server.composition.runtime import _conversation_extensions
from the_framework.server.runtime.session import SessionRuntime


class Socket:
    def __init__(self, *, accept_error=False):
        self.accept_error = accept_error
        self.accepted = asyncio.Event()
        self.incoming = asyncio.Queue()

    async def accept(self, **_options):
        if self.accept_error:
            raise RuntimeError("accept failed")
        self.accepted.set()

    async def close(self, code=None):
        self.closed = code

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, _output):
        raise RuntimeError("send failed")


def transport(runtime):
    return CanonicalTransportSockets(runtime, event_handshake=lambda _: None,
                                     application_handshake=lambda _: None)


def test_composition_exposes_transport_as_a_service(tmp_path):
    runtime = SessionRuntime(SimpleNamespace())
    extensions = _conversation_extensions(
        runtime, data_root=tmp_path, event_handshake=lambda _: None, application_handshake=lambda _: None,
    )
    sockets = next(extension for extension in extensions if extension.name == "canonical_transport")
    assert isinstance(sockets.service, CanonicalTransportSockets)
    assert sockets.service.runtime is runtime
    assert sockets.service.event_connection_count == 0


def test_multiple_sockets_release_presence_on_disconnect_and_cancellation():
    async def run():
        runtime = SessionRuntime(SimpleNamespace())
        sockets = transport(runtime)
        internal = runtime.stream()
        waiting = asyncio.create_task(anext(internal))
        await asyncio.sleep(0)
        assert runtime.subscribers and sockets.event_connection_count == 0
        first, second = Socket(), Socket()
        tasks = [asyncio.create_task(sockets.serve_events(socket, {"subprotocol": "events", "client_role": role}))
                 for socket, role in ((first, "primary"), (second, "preview"))]
        await first.accepted.wait()
        await second.accepted.wait()
        assert sockets.event_connection_count == 2
        assert sockets.primary_event_connection_count == 1
        await first.incoming.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(tasks[0], 1)
        assert sockets.event_connection_count == 1
        assert sockets.primary_event_connection_count == 0
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
        runtime = SessionRuntime(SimpleNamespace())
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


def test_preview_cannot_claim_the_application_bridge_even_without_a_principal():
    async def run():
        runtime = SessionRuntime(SimpleNamespace())
        sockets = transport(runtime)
        socket = Socket()
        await sockets.serve_application(socket, {'subprotocol': 'app', 'client_role': 'preview'})
        assert socket.closed == 1008
        assert not runtime.bridge.connected
    asyncio.run(run())


def test_direct_control_targets_only_the_primary_socket():
    async def run():
        sockets = transport(SessionRuntime(SimpleNamespace()))
        with pytest.raises(RuntimeError):
            await sockets.send_primary({"type": "control"})
        first, preview = Socket(), Socket()
        sent = []
        async def record(output):
            sent.append(output)
        first.send_json = record
        tasks = [asyncio.create_task(sockets.serve_events(socket, {
            "subprotocol": "events", "client_role": role,
        })) for socket, role in ((first, "primary"), (preview, "preview"))]
        try:
            await first.accepted.wait()
            await preview.accepted.wait()
            await sockets.send_primary({"type": "control"})
            assert sent == [{"type": "control"}]
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        with pytest.raises(RuntimeError):
            await sockets.send_primary({"type": "control"})
    asyncio.run(run())
