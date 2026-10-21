"""Planned restarts are announced before quiescing, failures remain visible."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from the_framework.agent.runtime.protocol import WorkerStatus
from the_framework.server.api.agent import CanonicalAgentApi
from the_framework.server.runtime.session import SessionRuntime


class RestartRuntime(SessionRuntime):
    def __init__(self, restart=None):
        self.application = None
        self.restart = restart
        self.restart_hooks = []
        self.server_restarting = False
        self.outputs = []

    async def publish(self, output):
        self.outputs.append(output)

    async def apply_event(self, name, payload, **kwargs):
        self.outputs.append(name)


def test_restart_announcement_precedes_hooks_and_barrier():
    async def scenario():
        runtime = RestartRuntime(lambda intent: runtime.outputs.append("restart"))
        runtime.register_restart_hook(lambda intent: runtime.outputs.append("drain"))
        before = runtime.server_status()
        assert before["phase"] == "ready"
        await runtime.restart_server({})
        assert runtime.outputs == [
            {"type": "server_restarting", **before, "phase": "restarting"},
            "drain", "server_restart_barrier", "restart",
        ]
        assert runtime.server_status()["phase"] == "restarting"
    asyncio.run(scenario())


@pytest.mark.parametrize("during_prepare", [True, False])
def test_restart_failure_reports_error_and_clears_restarting(during_prepare):
    async def scenario():
        def fail(intent):
            raise RuntimeError("supervisor unavailable")
        runtime = RestartRuntime(fail)
        if during_prepare:
            runtime.register_restart_hook(fail)
        with pytest.raises(RuntimeError, match="supervisor unavailable"):
            await runtime.restart_server({})
        assert runtime.server_status()["phase"] == "ready"
        assert runtime.outputs[-1] == {
            "type": "server_restart_failed", **runtime.server_status(),
            "error": "supervisor unavailable",
        }
    asyncio.run(scenario())


def test_restart_announcement_reaches_all_live_sessions():
    async def scenario():
        first, second = RestartRuntime(), RestartRuntime()
        first.application = SimpleNamespace(session_apps={
            "one": (SimpleNamespace(state=SimpleNamespace(session=first)), object()),
            "two": (SimpleNamespace(state=SimpleNamespace(session=second)), object()),
        })
        await first.prepare_restart()
        assert first.outputs == second.outputs
        assert second.server_restarting
    asyncio.run(scenario())


def test_status_confirms_server_identity_only_after_worker_response():
    async def scenario():
        runtime = RestartRuntime()
        status = WorkerStatus(id="status")
        async def request(payload):
            return status
        runtime.request = request
        result = await CanonicalAgentApi(runtime).status()
        assert isinstance(result, WorkerStatus)
        assert "server" not in status
        serialized = json.loads(json.dumps(result))
        assert serialized["server"] == runtime.server_status()
        assert serialized["type"] == "worker_status"
    asyncio.run(scenario())
