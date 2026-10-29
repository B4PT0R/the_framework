"""Run the documentation's Notebook feature without model/network calls."""

import asyncio
import json
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from examples.notebook import NOTEBOOK
from examples.notebook.models import RecentNotes
from examples.notebook.server import NotebookService
from the_framework import AgentResources, AgentSpec, Application, BuildContext, ServerExtension, Session
from the_framework.agent import FunctionCall
from the_framework.server.api.endpoints import Principal
from the_framework.server.runtime.bridge import ApplicationBridge


class TestSecurity:
    __test__ = False

    async def authenticate(self, request):
        if request.headers.get("authorization") == "Bearer test-only":
            return Principal(id="test")

    async def authorize(self, principal, requirement, request):
        return True


def storage(tmp_path):
    bridge = ApplicationBridge(supervisor=object())
    runtime = SimpleNamespace(bridge=bridge)
    context = BuildContext(data_root=tmp_path)
    return NotebookService(context, runtime), bridge


def test_note_storage_reopens_and_validates_both_entry_points(tmp_path):
    service, _ = storage(tmp_path)
    service.start()
    try:
        first = service.save("inbox", "design", "Design", "First version")
        second = service.save("inbox", "design", "Design", "Revised version")
        assert second["revision"] > first["revision"]
        assert len(service.recent("inbox")["notes"]) == 1
        assert service.recent("other")["notes"] == []
        with pytest.raises(ValueError):
            asyncio.run(service.handle("save", {"project": "../bad", "key": "x", "title": "T", "body": ""}))
        with pytest.raises(ValueError, match="unknown"):
            asyncio.run(service.handle("delete", {}))
    finally:
        service.stop()
    reopened, _ = storage(tmp_path)
    reopened.start()
    try:
        assert reopened.recent("inbox")["notes"][0]["body"] == "Revised version"
    finally:
        reopened.stop()


def test_worker_tool_pipeline_command_provider_and_saved_state(tmp_path):
    async def scenario():
        service, bridge = storage(tmp_path)
        service.start()
        try:
            path = tmp_path / "agent.json"
            spec = AgentSpec(name="assistant", extensions=(NOTEBOOK,))
            agent = spec.build_agent(Session.open(path), resources=AgentResources(client=object()))

            async def request(message):
                handler, _ = bridge.handlers[message.capability]
                return await handler(message.method, message.payload)

            agent.bridge.bind(request)
            outputs = agent.agentic_loop.command_outputs('/project "research"')
            assert outputs[0].status == "success"
            assert agent.extension("notebook").plugin_state.project == "research"
            call = FunctionCall(name="save_note", namespace="notebook", call_id="save-1", arguments=json.dumps({
                "key": "architecture", "title": "Architecture", "body": "Read the source.",
                "user_feedback": "I am saving the architecture note in the research notebook.",
            }))
            receipt = await agent.agentic_loop.tool_output(call)
            assert json.loads(receipt.output)["status"] == "success"
            text = ElementTree.fromstring(receipt.output_items[0].content[0].text).text
            assert json.loads(text)["key"] == "architecture"
            sampled = await agent.registry("providers").aoutputs(channel="text")
            text = ElementTree.fromstring(sampled[0].content[0].text).text
            assert json.loads(text) == {"project": "research", "status": "ready", "notes": [
                {"key": "architecture", "title": "Architecture"},
            ]}
            assert await agent.registry("providers").aoutputs(channel="realtime") == []
            assert not agent.session.history  # sampling and raw pipeline call do not append
            agent.update_config({"notebook": {"recent_limit": 2}})
            rebuilt = spec.build_agent(Session.open(path), resources=AgentResources(client=object()))
            assert rebuilt.extension("notebook").plugin_state.project == "research"
            assert rebuilt.extension("notebook").config.recent_limit == 2
            failed = agent.agentic_loop.command_outputs("/project ../bad")
            assert failed[0].status == "failure"
            assert agent.extension("notebook").plugin_state.project == "research"
            agent.deactivate_extension("notebook")
            assert not agent.registry("tools")
            assert service.recent("research")["notes"]  # binding does not stop storage
        finally:
            service.stop()
    asyncio.run(scenario())


def test_real_http_service_graph_auth_validation_and_lifecycle(tmp_path):
    service, bridge = storage(tmp_path)
    application = Application(
        name="Notebook HTTP test", version="1", security=TestSecurity(),
        server_extensions=(ServerExtension(name="notebook_store", service=service),),
    )
    with TestClient(application.build()) as client:
        assert client.get("/api/v1/notebook/notes", params={"project": "inbox"}).status_code == 401
        client.headers["authorization"] = "Bearer test-only"
        saved = client.put("/api/v1/notebook/notes", json={
            "project": "inbox", "key": "first", "title": "First", "body": "Saved over HTTP.",
        })
        assert saved.status_code == 200, saved.text
        recent = client.get("/api/v1/notebook/notes", params={"project": "inbox", "limit": 2})
        assert recent.status_code == 200, recent.text
        assert recent.json()["notes"][0]["body"] == "Saved over HTTP."
        invalid = client.put("/api/v1/notebook/notes", json={
            "project": "inbox", "key": "bad", "title": " ", "body": "",
        })
        assert invalid.status_code == 422, invalid.text
        assert client.get("/api/v1/notebook/notes", params={"project": "inbox", "limit": 21}).status_code == 422
        assert service.health()["status"] == "ready"
        assert "/api/v1/notebook/notes" in client.get("/openapi.json").json()["paths"]
    assert service.database is None


@pytest.mark.parametrize("values", [
    {"project": "x", "limit": 0}, {"project": "x", "limit": 21}, {"project": "../x"},
])
def test_recent_contract_rejects_invalid_values(values):
    with pytest.raises((ValueError, TypeError)):
        RecentNotes(**values)


def test_hosted_workers_share_http_storage_but_isolate_sessions(tmp_path):
    from httpx import ASGITransport, AsyncClient
    from tests.fixtures.notebook_app import application
    from the_framework.agent import PromptRequest

    async def handshake(socket):
        if socket.headers.get("authorization") == "Bearer test-only":
            return {"subprotocol": None}

    async def scenario():
        app = application.build(
            "tests.fixtures.notebook_app:application", BuildContext(data_root=tmp_path),
            security=TestSecurity(), handshake=handshake,
        )
        async with app.router.lifespan_context(app):
            host = app.state.application
            first = host.default_session
            other = await host.open_session(agent="assistant")
            assert first.supervisor.process.pid != other.supervisor.process.pid
            await first.command(PromptRequest(id="choose", prompt="/project research"))
            await first.command(PromptRequest(id="save", prompt="Save the design note."))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"authorization": "Bearer test-only"}) as client:
                path = "/api/v1/notebook/notes?project=research"
                notes = (await client.get(path)).json()["notes"]
                assert notes[0]["body"] == "A worker saved this note."
                other_path = f"/sessions/{other.ready.session_id}" + path
                assert (await client.get(other_path)).json()["notes"] == []
                assert first.service("notebook_store").path != other.service("notebook_store").path
                reply = await client.put("/api/v1/notebook/notes", json={
                    "project": "research", "key": "http", "title": "HTTP", "body": "Saved over HTTP.",
                })
                assert reply.status_code == 200, reply.text
                # Removing the worker binding leaves the installed routes/storage live.
                await first.set_plugin_binding("notebook", False)
                assert len((await client.get(path)).json()["notes"]) == 2
        assert first.supervisor.process.returncode == 0
        assert other.supervisor.process.returncode == 0
        saved = Session.open(tmp_path / "session.json")
        assert saved.state["notebook"]["project"] == "research"
        assert any(item.get("type") == "function_call_output" for item in saved.history)
        assert "A worker saved this note." not in str(saved.state)
    asyncio.run(asyncio.wait_for(scenario(), 30))
