import asyncio
from types import SimpleNamespace

import pytest

from the_framework.agent.runtime.protocol import StatusRequest
from the_framework.server.runtime.application import ApplicationRuntime
from the_framework.server.runtime.supervisor import WorkerExited


@pytest.mark.parametrize("phase", ["start", "stop", "both"])
def test_fleet_failure_does_not_leak_canonical_worker(phase):
    async def run():
        events = []

        class Supervisor:
            session_path = "unused"

            def __init__(self):
                self.outputs = asyncio.Queue()

            async def start(self):
                events.append("worker started")
                return SimpleNamespace(specialists=[])

            async def stop(self):
                events.append("worker stopped")

        class Fleet:
            async def start(self):
                raise RuntimeError("fleet start failed")

            async def stop(self):
                events.append("fleet stopped")
                if phase in {"stop", "both"}:
                    raise RuntimeError("fleet stop failed")

            def observe(self, output):
                pass

        runtime = ApplicationRuntime(Supervisor(), fleet_factory=lambda *_: Fleet())
        if phase == "start":
            with pytest.raises(RuntimeError, match="fleet start failed"):
                await runtime.start()
        elif phase == "both":
            with pytest.raises(ExceptionGroup, match="startup and cleanup failed") as caught:
                await runtime.start()
            assert str(caught.value.exceptions[0]) == "fleet start failed"
        else:
            runtime.fleet = Fleet()
            runtime.relay_task = asyncio.create_task(asyncio.Event().wait())
            with pytest.raises(ExceptionGroup, match="runtime shutdown failed"):
                await runtime.stop()
        assert events[-2:] == ["fleet stopped", "worker stopped"]
        assert runtime.relay_task is None
        assert runtime.fleet_result_task is None
        assert not runtime.observers

    asyncio.run(run())


def test_stop_releases_pending_requests_commands_and_streams():
    async def run():
        class Supervisor:
            def __init__(self):
                self.outputs = asyncio.Queue()
                self.sent = asyncio.Queue()

            async def send(self, request):
                self.sent.put_nowait(request)

            async def stop(self):
                pass

        supervisor = Supervisor()
        runtime = ApplicationRuntime(supervisor)
        request = asyncio.create_task(runtime.request(StatusRequest(id="request"), timeout=60))
        command = asyncio.create_task(runtime.command(StatusRequest(id="command"), timeout=60))
        stream = runtime.stream()
        streamed = asyncio.create_task(anext(stream))
        await supervisor.sent.get()
        await supervisor.sent.get()
        await asyncio.sleep(0)
        await runtime.stop()
        for task in (request, command):
            with pytest.raises(RuntimeError, match="runtime stopped"):
                await asyncio.wait_for(task, 1)
        assert isinstance(await asyncio.wait_for(streamed, 1), WorkerExited)
        await stream.aclose()
        for method in (runtime.request, runtime.command, runtime.submit):
            with pytest.raises(RuntimeError, match="runtime stopped"):
                await method(StatusRequest(id="late"))
        assert supervisor.sent.empty()
        assert not runtime.responses
        assert not runtime.subscribers

    asyncio.run(run())


def test_server_call_can_wait_for_worker_without_blocking_reception():
    from the_framework.agent.runtime.protocol import ApplicationCall, ApplicationCancel, ApplicationRequest, WorkerStatus

    async def run():
        class Supervisor:
            def __init__(self):
                self.outputs = asyncio.Queue()
                self.sent = asyncio.Queue()

            async def send(self, request):
                await self.sent.put(request)
                if isinstance(request, StatusRequest):
                    await self.outputs.put(WorkerStatus(request_id=request.id))

        supervisor = Supervisor()
        runtime = ApplicationRuntime(supervisor)
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def handler(method, payload):
            if method == "status":
                response = await runtime.request(StatusRequest(id="status"))
                return {"request_id": response.request_id}
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runtime.application.register("control", handler)
        relay = asyncio.create_task(runtime.relay())
        try:
            await supervisor.outputs.put(ApplicationCall(request=ApplicationRequest(
                id="status-call", capability="control", method="status",
            )))
            assert isinstance(await asyncio.wait_for(supervisor.sent.get(), 1), StatusRequest)
            result = await asyncio.wait_for(supervisor.sent.get(), 1)
            assert result.status == "success"
            assert result.result["request_id"] == "status"
            await supervisor.outputs.put(ApplicationCall(request=ApplicationRequest(
                id="slow-call", capability="control", method="wait",
            )))
            await asyncio.wait_for(entered.wait(), 1)
            await supervisor.outputs.put(ApplicationCancel(call_id="slow-call", reason="timeout"))
            await asyncio.wait_for(cancelled.wait(), 1)
            assert supervisor.sent.empty()
        finally:
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)
            await runtime.application.stop()

    asyncio.run(run())


def test_failed_observer_does_not_lose_worker_response(caplog):
    from the_framework.agent.runtime.protocol import WorkerStatus

    async def run():
        class Supervisor:
            def __init__(self):
                self.outputs = asyncio.Queue()

            async def send(self, request):
                await self.outputs.put(WorkerStatus(request_id=request.id))

        runtime = ApplicationRuntime(Supervisor())

        def failed(_output):
            raise RuntimeError("observer broke")

        runtime.register_observer(failed)
        relay = asyncio.create_task(runtime.relay())
        try:
            response = await runtime.request(StatusRequest(id="probe"), timeout=1)
            assert response.request_id == "probe"
            assert not relay.done()
        finally:
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)

    asyncio.run(run())
    assert "observer broke" in caplog.text
