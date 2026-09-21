import asyncio
import io
import json

from core.agent.runtime.protocol import (
    CommandCompleted,
    PromptRequest,
    WorkerOutput,
    WorkerStopped,
)
from core.agent.runtime.worker_process import JsonLines


def test_json_lines_transport_uses_serializable_payloads():
    async def test():
        input = io.StringIO('{"type":"status_request","id":"one"}\n')
        output = io.StringIO()
        transport = JsonLines(input, output)

        request = await transport.receive()
        await transport.send(WorkerStopped(request_id=request["id"]))

        assert request == {"type": "status_request", "id": "one"}
        assert json.loads(output.getvalue()) == {
            "type": "worker_stopped",
            "request_id": "one",
        }

    asyncio.run(test())


def test_worker_output_recasts_nested_protocol_payloads():
    output = WorkerOutput.from_dict({
        "type": "command_completed",
        "request_id": "one",
        "command": {
            "type": "prompt_request",
            "id": "one",
            "prompt": "hello",
            "status": "completed",
        },
    })

    assert isinstance(output, CommandCompleted)
    assert isinstance(output.command, PromptRequest)


def test_application_specialist_retains_factories_and_resolved_runtime_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.agent import AgentSpec
    from core.agent.runtime import worker_process
    from core.plugins.memory.curator import MemoryCuratorPlugin
    from core.server.runtime.fleet import AgentRunner

    spec = AgentSpec(name="memory.jiminy", plugins=(lambda agent: MemoryCuratorPlugin(agent),))
    path = tmp_path / "durable-memory.sqlite3"
    profile = AgentSpec({**spec, "instructions": "Application-specific curation role",
                         "configuration": {"memory_curator": {"store_path": str(path)}}}).fleet_profile()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    runner = AgentRunner(profile, tmp_path, asyncio.Queue(), application_reference="test:application")
    assert runner.command(tmp_path / "session.json", profile_path)[-2:] == ["--profile", str(profile_path)]
    monkeypatch.setattr(worker_process, "load_application", lambda _: SimpleNamespace(
        compile=lambda: SimpleNamespace(agents={spec.name: spec}, primary_agent=spec),
    ))
    agents = []

    async def inspect(worker):
        agents.append(worker.agent)

    monkeypatch.setattr(worker_process.Worker, "run", inspect)
    asyncio.run(worker_process.run(tmp_path / "session.json", application_reference="test:application",
                                   agent_name=spec.name, profile_path=profile_path))
    assert agents[0].plugin("memory_curator").store.path == path
    assert path.exists()
    assert "Application-specific curation role" in str(agents[0].instructions)
