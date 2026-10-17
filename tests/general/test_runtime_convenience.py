"""High-level operations keep the canonical worker command guarantees."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.agent.runtime.protocol import (
    AgentConfigUpdated, CommandCompleted, CommandEvent, CommandFailed,
)
from the_framework.server.runtime.application import ApplicationRuntime, InvalidWorkerResult


def test_runtime_operations_wait_for_completion_and_preserve_retry_ids():
    async def run():
        class Supervisor:
            def __init__(self):
                self.outputs = asyncio.Queue()
                self.sent = []

            async def send(self, command):
                self.sent.append(command)
                if command.type == "config_update_request":
                    await self.outputs.put(CommandEvent(
                        request_id=command.id, command_id=command.id,
                        event=AgentConfigUpdated(config=command.updates, restart_required=True),
                    ))
                await self.outputs.put(CommandCompleted(request_id=command.id, command=command))

        supervisor = Supervisor()
        runtime = ApplicationRuntime(supervisor)
        relay = asyncio.create_task(runtime.relay())
        try:
            result = await runtime.update_config({"model": "example"}, id="settings-1")
            assert result["config"] == {"model": "example"}
            assert result.restart_required
            await runtime.apply_event("document_imported", {"path": "notes"}, id="import-1")
            assert supervisor.sent[-1].id == "import-1"
            assert supervisor.sent[-1].name == "document_imported"
            assert supervisor.sent[-1].payload == {"path": "notes"}
            runtime.resetting = True
            with pytest.raises(RuntimeError, match="reset is in progress"):
                await runtime.apply_event("document_imported", {})
            assert len(supervisor.sent) == 2
        finally:
            relay.cancel()
            await asyncio.gather(relay, return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("failed", [False, True])
def test_config_update_rejects_missing_result_and_preserves_worker_failure(failed):
    async def run():
        runtime = None

        async def send(command):
            output = (CommandFailed(request_id=command.id, command=command, error="invalid settings")
                      if failed else CommandCompleted(request_id=command.id, command=command))
            await runtime.publish(output)

        runtime = ApplicationRuntime(SimpleNamespace(send=send))
        expected = ValueError if failed else InvalidWorkerResult
        with pytest.raises(expected, match="invalid settings" if failed else "without a result"):
            await runtime.update_config({"model": "example"})
        assert not runtime.subscribers

    asyncio.run(run())


@pytest.mark.parametrize("running,ready", [(True, True), (True, False), (False, True)])
def test_runtime_health_requires_a_running_ready_worker(running, ready):
    runtime = ApplicationRuntime(SimpleNamespace(running=running))
    runtime.ready = SimpleNamespace(session_id="primary", protocol=1) if ready else None
    diagnostics = runtime.health()
    assert diagnostics["status"] == ("ok" if running and ready else "degraded")
    assert diagnostics["worker"]["running"] is running
    assert diagnostics["application"] == {"connected": False}
    assert diagnostics["auxiliaries"] == []
