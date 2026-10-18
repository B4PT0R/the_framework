"""One assembly path owns durable conversation, transport and build phases."""

import asyncio
import os
from itertools import permutations

import pytest
from httpx import ASGITransport, AsyncClient

from the_framework import Application, AgentSpec, BuildContext, Plugin, ServerExtension
from the_framework.agent.context.session import Session
from the_framework.agent.models.responses import Message
from the_framework.plugins.realtime import REALTIME
from the_framework.plugins.realtime.context import CONTEXT_FACTORY
from the_framework.server.api.endpoints import Principal
from the_framework.server.runtime.supervisor import WorkerSupervisor


class Security:
    async def authenticate(self, request):
        if request.headers.get("authorization") == "Bearer test":
            return Principal(id="test", scopes=frozenset({"*"}))

    async def authorize(self, principal, requirement, request):
        return principal.id == "test"

    async def handshake(self, socket):
        return None  # This fixture deliberately refuses socket connections.


def declaration(*plugins):
    return Application(
        name="Conversation", version="1", security=Security(),
        primary_agent=AgentSpec(name="primary", session_mode="durable"),
        plugins=plugins,
    )


@pytest.mark.parametrize("voice", [False, True])
def test_assembly_recreates_one_durable_conversation(tmp_path, monkeypatch, voice):
    module = tmp_path / "assembly_fixture.py"
    module.write_text('''
from the_framework import Application, AgentSpec
from the_framework.plugins.realtime import REALTIME
application = Application(name="Fixture", version="1",
    primary_agent=AgentSpec(name="primary", session_mode="durable"),
    plugins=(REALTIME,) if VOICE else ())
'''.replace("VOICE", str(voice)))
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    # Import via the same declaration reference used by the subprocess worker.
    import importlib.util
    spec = importlib.util.spec_from_file_location("assembly_fixture", module)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    root = tmp_path / "data"
    session = Session.open(root / "session.json")
    session.append(Message(role="user", content=[{"type": "input_text", "text": "Keep this conversation"}]))

    async def run():
        for _ in range(2):
            context = BuildContext(data_root=root)
            app = fixture.application.build(
                "assembly_fixture:application", context,
                security=Security(), handshake=lambda socket: None,
            )
            assert not context.services
            async with app.router.lifespan_context(app):
                runtime = context.services["runtime"]
                assert runtime.ready.session_id == session.id
                assert runtime.supervisor.application_reference == "assembly_fixture:application"
                assert runtime.supervisor.session_path == root / "session.json"
                assert "canonical_transport" in context.services
                assert "chat" not in app.state.application.plugins
                chat = context.services["chat"]
                assert (chat.voice is not None) == voice
                if voice:
                    assert chat.ingress.lock is context.services["realtime"].lock
                    # Exercise the voice coordinator through the common plugin host.
                    assert len(context.contributions("plugins.binding_update")) == 1
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/api/v1/session")).status_code == 401
                    response = await client.get("/api/v1/session", headers={"Authorization": "Bearer test"})
                    assert response.status_code == 200
                    assert "Keep this conversation" in response.text
            assert runtime.supervisor.process.returncode == 0
            assert not context.services

    asyncio.run(run())


def test_contributions_are_collected_before_factories_in_any_plugin_order(tmp_path):
    class LiveContext:
        def __init__(self, controller):
            self.controller = controller

    def producer(context):
        context.contribute(CONTEXT_FACTORY, LiveContext)
        return ()

    contributor = Plugin(name="context", server_extension=producer)
    for plugins in permutations((REALTIME, contributor)):
        context = BuildContext(data_root=tmp_path)
        app = declaration(*plugins).build(
            "example:application", context, handshake=lambda socket: None,
        )
        api = app.state.application.server_extensions["realtime"].service
        assert len(api.controller.contexts) == 1
        assert isinstance(api.controller.contexts[0], LiveContext)
        with pytest.raises(RuntimeError, match="sealed"):
            context.contribute(CONTEXT_FACTORY, LiveContext)


def test_service_factory_cannot_change_contributions(tmp_path):
    def declare(context):
        def build():
            context.contribute("too.late", object())
        return ServerExtension(name="late", service_factory=build)

    with pytest.raises(RuntimeError, match="sealed before service construction"):
        declaration(Plugin(name="late", server_extension=declare))._build_server(BuildContext())


def test_services_have_one_lifecycle_owner_and_context_cannot_be_reused(tmp_path):
    class Service:
        def __init__(self):
            self.started = False

        async def start(self):
            self.started = True

        async def stop(self):
            self.started = False

    async def run():
        service = Service()
        context = BuildContext(data_root=tmp_path)
        app = declaration(Plugin(name="feature", server_extension=service))._build_server(context)
        with pytest.raises(RuntimeError, match="one application build"):
            declaration()._build_server(context)
        assert "feature" not in context.services
        async with app.router.lifespan_context(app):
            assert context.services["feature"] is service
            assert service.started
            with pytest.raises(TypeError):
                context.services["other"] = object()
        assert "feature" not in context.services
        assert not service.started

    asyncio.run(run())


def test_injected_supervisor_cannot_point_to_another_conversation(tmp_path):
    with pytest.raises(ValueError, match="same session"):
        declaration().build(
            "example:application", BuildContext(data_root=tmp_path),
            handshake=lambda socket: None,
            supervisor=WorkerSupervisor(tmp_path / "other.json"),
        )
    with pytest.raises(ValueError, match="same application reference"):
        declaration().build(
            "example:application", BuildContext(data_root=tmp_path),
            handshake=lambda socket: None,
            supervisor=WorkerSupervisor(tmp_path / "session.json", application_reference="other:application"),
        )


def test_conversation_requires_explicit_security_and_socket_handshakes(tmp_path):
    app = Application({**declaration(), "security": None})
    with pytest.raises(ValueError, match="security policy"):
        app.build("example:application", BuildContext(data_root=tmp_path))
    with pytest.raises(ValueError, match="handshakes"):
        declaration().build("example:application", BuildContext(data_root=tmp_path))
    assert not list(tmp_path.iterdir())


def test_minimal_host_accepts_text_and_files_without_plugins(tmp_path):
    from starlette.testclient import TestClient
    from tests.fixtures.runtime import FakeRuntime

    runtime = FakeRuntime()
    app = declaration().build(
        "example:application", BuildContext(data_root=tmp_path),
        handshake=lambda socket: None, session_factory=lambda supervisor, context: runtime,
    )
    with TestClient(app) as client:
        assert client.post('/api/v1/agent/prompt', json={
            'id': 'denied', 'prompt': 'Private',
        }).status_code == 401
        client.headers['authorization'] = 'Bearer test'
        assert client.post('/api/v1/agent/prompt', json={
            'id': 'text', 'prompt': 'Hello',
        }).status_code == 202
        assert client.post('/api/v1/agent/attachments', data={'id': 'file'}, files={
            'files': ('notes.txt', b'Keep this file', 'text/plain'),
        }).status_code == 202
        assert [command.id for command in runtime.submitted] == ['text', 'file']
        assert (tmp_path / 'bucket/notes.txt').read_bytes() == b'Keep this file'
        assert not app.state.application.plugins
