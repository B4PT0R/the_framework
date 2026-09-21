import asyncio

from harness_core.server.runtime.bridge import ApplicationBridge


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
