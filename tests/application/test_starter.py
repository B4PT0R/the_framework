from core import AgentResources
from core.utils.persistence import MappingStore
from starter.application import application, resources


def test_starter_assembles_with_product_imports_forbidden():
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "-c", """
import importlib.abc
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

class NoProduct(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'the_harness' or fullname.startswith('the_harness.'):
            raise ImportError('starter must not import the product: ' + fullname)

sys.meta_path.insert(0, NoProduct())
from starter import desktop
from starter.server import create_app
with TemporaryDirectory() as root:
    app = create_app(Path(root), token='isolated-test', origin='http://localhost')
    assert app.state.runtime is not None
"""], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_starter_declares_general_plugins_and_memory_specialist():
    plan = application.compile()
    assert plan.primary_agent.name == "assistant"
    assert {plugin.name for plugin in application.plugins} == {
        "bash", "registry", "web_search", "memory", "scheduler", "system",
        "realtime", "chromium",
    }
    assert "memory.jiminy" in plan.agents


def test_starter_without_credentials_remains_available_after_inference_failure(tmp_path):
    import sys
    from pathlib import Path

    import pytest
    from fastapi.testclient import TestClient

    from core.agent.runtime.protocol import PromptRequest
    from core.server.runtime.application import ApplicationRuntime
    from core.server.runtime.supervisor import WorkerSupervisor
    from starter.server import create_app

    session = tmp_path / "session.json"
    runtime = ApplicationRuntime(WorkerSupervisor(session, command=[
        sys.executable, str(Path(__file__).parent / "fixtures/unauthenticated_worker.py"),
        "--session", str(session), "--application", "starter.application:application",
    ]))
    app = create_app(tmp_path, token="test", origin="http://testserver", runtime=runtime)
    with TestClient(app) as client:
        client.headers["authorization"] = "Bearer test"
        assert client.get("/api/v1/config").status_code == 200
        with pytest.raises(ValueError, match="credentials"):
            client.portal.call(runtime.command, PromptRequest(id="no-auth", prompt="Hello"), 10)
        assert client.get("/api/v1/status").status_code == 200
        assert client.get("/api/v1/session").status_code == 200


def test_starter_builds_without_inference_or_product_configuration(tmp_path):
    session = tmp_path / "session.json"
    stored = resources(session)
    agent = application.compile().primary_agent.build_agent(
        session,
        resources=AgentResources({**stored, "client": object()}),
    )
    assert agent.name == "assistant"
    agent.update_config({"bash": {"default_cwd": str(tmp_path)}})
    assert resources(session).configuration["bash"]["default_cwd"] == str(tmp_path)
    assert MappingStore(tmp_path / "settings.json", field="settings").load()


def test_starter_http_uses_authenticated_canonical_queue(tmp_path):
    from fastapi.testclient import TestClient
    from starter.server import create_app

    class Runtime:
        fleet = None
        running = False

        def __init__(self):
            from core.server.runtime.bridge import ApplicationBridge
            self.application = ApplicationBridge(None)
            self.submitted = []

        def register_observer(self, observer):
            pass

        async def start(self):
            self.running = True

        async def stop(self):
            self.running = False

        async def submit(self, command):
            self.submitted.append(command)

        async def publish(self, event):
            pass

    runtime = Runtime()
    app = create_app(tmp_path, token="private-test-secret", origin="http://testserver", runtime=runtime)
    with TestClient(app) as client:
        assert runtime.running
        assert client.get("/api/v1/health").status_code == 401
        client.headers["authorization"] = "Bearer private-test-secret"
        assert client.get("/api/v1/health").json()["status"] == "ok"
        # Cleanup from another window is harmless; an uncorrelated public stop
        # must not acquire the privileged internal shutdown behavior.
        assert client.request("DELETE", "/api/v1/realtime/calls/current", json={}).status_code == 422
        assert client.request("DELETE", "/api/v1/realtime/calls/current",
                              params={"call_id": "obsolete-attempt"}).status_code == 204
        result = client.post("/api/v1/agent/prompt", json={"id": "turn-1", "prompt": "Hello"})
        assert result.status_code == 202
        assert runtime.submitted[0].id == "turn-1"
        assert runtime.submitted[0].prompt == "Hello"
        assert client.post("/api/v1/agent/interrupt", json={}).status_code == 202
        assert runtime.submitted[-1].type == "interrupt_request"
        assert len(client.get("/api/v1/plugins").json()["plugins"]) == 8
        response = client.post("/api/v1/agent/attachments",
                               data={"id": "files-1", "prompt": "Read these notes"},
                               files=[("files", ("notes.txt", b"Important notes", "text/plain"))])
        assert response.status_code == 202, response.text
        command = runtime.submitted[-1]
        assert command.prompt == "Read these notes"
        assert (tmp_path / "files/notes.txt").read_bytes() == b"Important notes"
        assert command.input_items[0].kind == "attachments"
        assert command.input_items[1].kind == "copied_files"
        response = client.post("/api/v1/agent/attachments", data={"id": "files-2"},
                               files=[("files", ("notes.txt", b"Other notes", "text/plain"))])
        assert response.status_code == 202, response.text
        assert not runtime.submitted[-1].append_prompt
        assert (tmp_path / "files/notes (2).txt").read_bytes() == b"Other notes"
        count = len(runtime.submitted)
        response = client.post("/api/v1/agent/attachments", data={"id": "bad-image"},
                               files=[("files", ("invalid.png", b"not an image", "image/png"))])
        assert response.status_code == 422
        assert len(runtime.submitted) == count
        assert not (tmp_path / "files/invalid.png").exists()
        from io import BytesIO
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (2, 2)).save(image, format="PNG")
        response = client.post("/api/v1/agent/attachments", data={"id": "image"},
                               files=[("files", ("picture.png", image.getvalue(), "image/png"))])
        assert response.status_code == 202, response.text
        vision = runtime.submitted[-1].input_items[-1].to_api_format()
        assert vision["content"][0]["type"] == "input_image"
    assert not runtime.running


def test_starter_real_worker_starts_and_restores_configuration(tmp_path):
    from fastapi.testclient import TestClient
    from starter.server import create_app

    headers = {"authorization": "Bearer private-test-secret"}
    for iteration in range(2):
        app = create_app(tmp_path, token="private-test-secret", origin="http://testserver")
        with TestClient(app) as client:
            assert client.get("/api/v1/health", headers=headers).json()["status"] == "ok"
            assert client.get("/api/v1/status", headers=headers).status_code == 200
            response = client.get("/api/v1/session", headers=headers)
            assert response.status_code == 200
            if iteration == 0:
                response = client.patch("/api/v1/config", headers=headers,
                                        json={"bash": {"default_cwd": str(tmp_path)}})
                assert response.status_code == 200, response.text
                response = client.put("/api/v1/plugins/registry/binding", headers=headers,
                                      json={"enabled": False})
                assert response.status_code == 200, response.text
            else:
                restored = client.get("/api/v1/config", headers=headers).json()
                assert restored["config"]["bash"]["default_cwd"] == str(tmp_path)
                plugins = client.get("/api/v1/plugins", headers=headers).json()["plugins"]
                assert next(plugin for plugin in plugins if plugin["name"] == "registry")["binding_enabled"] is False


def test_starter_voice_transcript_is_committed_once_and_restored(tmp_path):
    from fastapi.testclient import TestClient
    from core.agent.runtime.protocol import ExternalEventRequest
    from starter.server import create_app

    command = ExternalEventRequest(id="realtime:test:turn-1:user", name="realtime_turn",
                                   payload={"call_id": "test", "turn_id": "turn-1",
                                            "role": "user", "transcript": "Voice continuity marker"})
    for _ in range(2):
        app = create_app(tmp_path, token="test-secret", origin="http://testserver")
        events = []

        async def observe(output):
            events.append(output)

        app.state.runtime.register_observer(observe)
        with TestClient(app) as client:
            client.portal.call(app.state.runtime.command, command)
            response = client.get("/api/v1/session", headers={"authorization": "Bearer test-secret"})
            items = [item for turn in response.json()["turns"] for item in turn["items"]]
            transcripts = [item for item in items if item.get("kind") == "realtime"]
            assert len(transcripts) == 1
            assert transcripts[0]["content"][0]["text"] == "Voice continuity marker"
            if _ == 0:
                assert any(output.get("event", {}).get("type") == "agent.response_item.added"
                           for output in events)
