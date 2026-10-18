import asyncio
import io
import json

from the_framework.agent.runtime.protocol import (
    CommandCompleted,
    PromptRequest,
    WorkerOutput,
    WorkerStopped,
)
from the_framework.agent.runtime.worker_process import JsonLines


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

    from the_framework.agent import AgentSpec
    from the_framework.agent.runtime import worker_process
    from the_framework.plugins.memory.curator import MemoryCuratorExtension
    from the_framework.server.runtime.fleet import AgentRunner

    spec = AgentSpec(name="memory.jiminy", extensions=(lambda agent: MemoryCuratorExtension(agent),))
    path = tmp_path / "durable-memory.sqlite3"
    profile = AgentSpec({**spec, "instructions": "Application-specific curation role",
                         "configuration": {"memory_curator": {"store_path": str(path)}}}).fleet_profile()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    runner = AgentRunner(profile, tmp_path, asyncio.Queue(), application_reference="test:application")
    assert runner.command(tmp_path / "session.json", profile_path)[-2:] == ["--profile", str(profile_path)]
    monkeypatch.setattr(worker_process, "load_application", lambda _: SimpleNamespace(
        compile=lambda: SimpleNamespace(agents={spec.name: spec}, application=SimpleNamespace(agents=(spec,)), specialists=()),
    ))
    agents = []

    async def inspect(worker):
        agents.append(worker.agent)

    monkeypatch.setattr(worker_process.Worker, "run", inspect)
    asyncio.run(worker_process.run(tmp_path / "session.json", application_reference="test:application",
                                   agent_name=spec.name, profile_path=profile_path))
    assert agents[0].extension("memory_curator").store.path == path
    assert path.exists()
    assert "Application-specific curation role" in str(agents[0].instructions)


def test_resolved_launch_settings_override_defaults_but_not_saved_session(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from the_framework import AgentResources, AgentSpec
    from the_framework.agent.context.session import Session
    from the_framework.agent.runtime import worker_process
    from the_framework.plugins.memory.curator import MemoryCuratorExtension

    for saved in (False, True):
        root = tmp_path / str(saved)
        root.mkdir()
        client = object()
        calls = []
        def resources(path):
            calls.append(path)
            return AgentResources(client=client, configuration={
                "model": "resource-default",
                "memory_curator": {"store_path": str(root / "resource.sqlite3")},
            })
        spec = AgentSpec(name="private",  resources=resources,
            configuration={"model": "declared-default"},
            extensions=(lambda agent: MemoryCuratorExtension(agent),),
            initializers=(lambda agent: setattr(agent, "application_initializer", True),))
        session_path = root / "session.json"
        previous_id = None
        if saved:
            session = Session.open(session_path)
            session.update_agent(configuration={"model": "saved-model",
                "memory_curator": {"store_path": str(root / "saved.sqlite3")}})
            previous_id = session.id
        profile = AgentSpec(name="private",  instructions="Resolved role",
            configuration={"model": "resolved-model",
                "memory_curator": {"store_path": str(root / "resolved.sqlite3")}}).fleet_profile(durable=True, retain_history=True)
        profile_path = root / "profile.json"
        profile_path.write_text(json.dumps(profile))
        monkeypatch.setattr(worker_process, "load_application", lambda _: SimpleNamespace(
            compile=lambda: SimpleNamespace(agents={spec.name: spec}, application=SimpleNamespace(agents=(spec,)), specialists=())))
        constructed = []
        async def capture(worker):
            constructed.append(worker.agent)
        monkeypatch.setattr(worker_process.Worker, "run", capture)
        asyncio.run(worker_process.run(session_path, application_reference="test:application",
            agent_name=spec.name, profile_path=profile_path))
        agent = constructed[0]
        assert agent.configs.model == ("saved-model" if saved else "resolved-model")
        expected = root / ("saved.sqlite3" if saved else "resolved.sqlite3")
        assert agent.extension("memory_curator").store.path == expected
        assert agent.client is client
        assert agent.application_initializer is True
        assert calls == [session_path]
        assert "Resolved role" in str(agent.instructions)
        if saved:
            assert agent.session.id == previous_id


def test_worker_entrypoint_explicit_session_policy_overrides_launch_profile(tmp_path, monkeypatch):
    from the_framework import AgentSpec
    from the_framework.agent.runtime import worker_process
    profile = AgentSpec(name="private").fleet_profile(durable=True, retain_history=True)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile))
    constructed = []
    async def capture(worker):
        constructed.append(worker.agent)
    monkeypatch.setattr(worker_process.Worker, "run", capture)
    asyncio.run(worker_process.run(tmp_path / "session.json", profile_path=path, durable=False, retain_history=False))
    assert constructed[0].session.path is None
    assert constructed[0].session.retain_history is False


def test_saved_session_retention_wins_over_worker_launch_defaults(tmp_path, monkeypatch):
    from the_framework import AgentSpec
    from the_framework.agent import Session
    from the_framework.agent.runtime import worker_process
    path = tmp_path / "session.json"
    saved = Session.open(path, retain_history=False)
    saved.agent_name = "private"
    saved.save()
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(AgentSpec(name="private").fleet_profile(
        durable=True, retain_history=True)))
    constructed = []
    async def capture(worker):
        constructed.append(worker.agent)
    monkeypatch.setattr(worker_process.Worker, "run", capture)
    asyncio.run(worker_process.run(path, profile_path=profile_path, retain_history=True))
    assert constructed[0].session.id == saved.id
    assert constructed[0].session.retain_history is False
    assert Session.open(path).retain_history is False
