import asyncio

from the_framework.server.runtime.bridge import ApplicationBridge


class Supervisor:
    async def send(self, _payload):
        return None


class WebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.accepted = False

    async def accept(self, *, subprotocol=None):
        self.accepted = True

    async def receive_json(self):
        payload = await self.incoming.get()
        if isinstance(payload, BaseException):
            raise payload
        return payload

    async def send_json(self, payload):
        await self.outgoing.put(payload)

    async def close(self, code=None):
        return None


def test_slow_application_event_does_not_block_control_socket():
    async def test():
        bridge = ApplicationBridge(Supervisor())
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_event(_event_id, _payload):
            started.set()
            await release.wait()

        bridge.register_event("telemetry", slow_event)
        await bridge.start()
        websocket = WebSocket()
        running = asyncio.create_task(bridge.serve(websocket))
        while not websocket.accepted:
            await asyncio.sleep(0)

        await websocket.incoming.put({
            "type": "application_event",
            "id": "event-1",
            "name": "telemetry",
            "payload": {},
        })
        await started.wait()
        await websocket.incoming.put({"type": "heartbeat"})

        assert await asyncio.wait_for(websocket.outgoing.get(), 0.1) == {
            "type": "heartbeat_ack",
        }
        release.set()
        assert await asyncio.wait_for(websocket.outgoing.get(), 0.1) == {
            "type": "application_event_ack",
            "id": "event-1",
        }

        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await bridge.stop()

    asyncio.run(test())


def test_failed_application_event_is_nacked_without_closing_socket():
    async def test():
        bridge = ApplicationBridge(Supervisor())

        async def failed_event(_event_id, _payload):
            raise RuntimeError("worker is temporarily busy")

        bridge.register_event("telemetry", failed_event)
        await bridge.start()
        websocket = WebSocket()
        running = asyncio.create_task(bridge.serve(websocket))
        while not websocket.accepted:
            await asyncio.sleep(0)

        await websocket.incoming.put({
            "type": "application_event",
            "id": "event-2",
            "name": "telemetry",
            "payload": {},
        })
        assert await asyncio.wait_for(websocket.outgoing.get(), 0.1) == {
            "type": "application_event_ack",
            "id": "event-2",
            "status": "failure",
            "error": "worker is temporarily busy",
        }
        await websocket.incoming.put({"type": "heartbeat"})
        assert await asyncio.wait_for(websocket.outgoing.get(), 0.1) == {
            "type": "heartbeat_ack",
        }

        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
        await bridge.stop()

    asyncio.run(test())


def test_application_event_ack_returns_to_its_originating_connection():
    async def test():
        bridge = ApplicationBridge(Supervisor())
        bridge.register_event("telemetry", lambda _event_id, _payload: None)
        await bridge.start()
        remote_responses = asyncio.Queue()

        response = await bridge.accept(
            {
                "type": "application_event",
                "id": "remote-event",
                "name": "telemetry",
                "payload": {},
            },
            responses=remote_responses,
        )

        assert response is None
        assert await asyncio.wait_for(remote_responses.get(), 0.1) == {
            "type": "application_event_ack",
            "id": "remote-event",
        }
        assert bridge.outgoing.empty()
        await bridge.stop()

    asyncio.run(test())


def test_stopping_bridge_cancels_server_calls_without_late_results():
    from the_framework.agent.runtime.protocol import ApplicationCall, ApplicationRequest

    async def run():
        results = asyncio.Queue()
        entered = asyncio.Event()
        closed = asyncio.Event()

        class RecordingSupervisor:
            async def send(self, result):
                await results.put(result)

        async def wait(method, payload):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        bridge = ApplicationBridge(RecordingSupervisor())
        bridge.register("slow", wait)
        await bridge.dispatch(ApplicationCall(request=ApplicationRequest(
            id="pending", capability="slow", method="wait",
        )))
        await asyncio.wait_for(entered.wait(), 1)
        await bridge.stop()
        assert closed.is_set()
        assert results.empty()

    asyncio.run(run())


def test_invalid_server_result_is_reported_as_call_failure():
    from the_framework.agent.runtime.protocol import ApplicationCall, ApplicationRequest

    async def run():
        results = asyncio.Queue()

        class RecordingSupervisor:
            async def send(self, result):
                await results.put(result)

        bridge = ApplicationBridge(RecordingSupervisor())
        bridge.register("invalid", lambda method, payload: object())
        await bridge.dispatch(ApplicationCall(request=ApplicationRequest(
            id="invalid", capability="invalid", method="read",
        )))
        result = await asyncio.wait_for(results.get(), 1)
        assert result.status == "failure"
        assert "non-JSON" in result.error
        await bridge.stop()

    asyncio.run(run())


def test_stopping_bridge_releases_server_requests_to_client():
    import pytest
    from the_framework.agent.runtime.bridge import ApplicationError

    async def run():
        bridge = ApplicationBridge(Supervisor())
        bridge.websocket = object()
        request = asyncio.create_task(bridge.call("client", "read", timeout_ms=60_000))
        await asyncio.wait_for(bridge.outgoing.get(), 1)
        await bridge.stop()
        with pytest.raises(ApplicationError, match="runtime stopped"):
            await asyncio.wait_for(request, 1)

    asyncio.run(run())


def test_failed_primary_accept_does_not_prevent_reconnection():
    async def run():
        bridge = ApplicationBridge(Supervisor())
        class Failed(WebSocket):
            async def accept(self, **kwargs):
                raise RuntimeError('accept failed')
        try:
            await bridge.serve(Failed())
        except RuntimeError:
            pass
        assert not bridge.connected
        valid = WebSocket()
        task = asyncio.create_task(bridge.serve(valid))
        await asyncio.sleep(0)
        assert valid.accepted
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not bridge.connected
    asyncio.run(run())
