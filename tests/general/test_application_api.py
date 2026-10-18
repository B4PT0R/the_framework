"""Public Application.build: explicit owners and startup for one application."""
from fastapi.testclient import TestClient
import pytest

from the_framework import Application, AgentSpec, BuildContext, ServerExtension
from the_framework.server import SessionRuntime
from tests.fixtures.multi_session_app import application


class Security:
    async def authenticate(self, request):
        return None

    async def authorize(self, principal, requirement, request):
        return False


async def refuse_socket(socket):
    return None


def test_service_only_application_starts_and_stops_resources_in_lifespan():
    events = []

    class Resource:
        async def start(self):
            events.append(('start', self))

        async def stop(self):
            events.append(('stop', self))

    definition = Application(name='Services', version='1', server_extensions=(
        ServerExtension(name='resource', service_factory=Resource),
    ))
    assert definition.compile().primary_agent is None
    assert events == []
    server = definition.build()
    host = server.state.application
    assert host is definition
    assert host.primary_session is None
    assert not host.services.view and events == []
    resource = host.extensions['resource'].service
    assert all(route.path not in ('/api/v1/agent/prompt', '/api/v1/sessions') for route in server.routes)
    with TestClient(server):
        assert host.service('resource') is resource
        assert events == [('start', resource)]
    assert events == [('start', resource), ('stop', resource)]
    assert not host.services.view


def test_agent_application_constructs_its_session_without_starting_the_worker(tmp_path):
    context = BuildContext(data_root=tmp_path)
    server = application.build('tests.fixtures.multi_session_app:application', context,
                               security=Security(), handshake=refuse_socket)
    host = server.state.application
    assert host is application
    assert isinstance(host.primary_session, SessionRuntime)
    assert host.primary_session.bridge is not None
    assert not host.primary_session.supervisor.running
    assert host.primary_session.supervisor.session_path == context['session_path']
    assert not context.services
    assert any(route.path == '/api/v1/agent/prompt' for route in server.routes)


def test_agent_application_cannot_silently_build_without_its_conversation():
    definition = Application(name='Agent', version='1', primary_agent=AgentSpec(name='assistant'))
    with pytest.raises(ValueError, match='requires its module:application reference'):
        definition.build()
    with pytest.raises(ValueError, match='requires BuildContext'):
        definition.build('example:application')
    with pytest.raises(ValueError, match='conversation inputs require a primary_agent'):
        Application(name='Services', version='1').build('example:application')
    with pytest.raises(ValueError, match='additional agents require a primary_agent'):
        Application(name='Services', version='1', agents=(AgentSpec(name='assistant'),))


def test_conversation_defaults_to_editable_ui_beside_its_declaration(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from tests.fixtures.runtime import FakeRuntime

    source = tmp_path / 'project' / 'ui'
    (source / 'dist').mkdir(parents=True)
    (source / 'dist/index.html').write_text('<h1>My application</h1>')
    definition = Application(name='Desktop', version='1', primary_agent=AgentSpec(name='assistant'))
    module = ModuleType('desktop_example')
    module.__file__ = str(source.parent / 'application.py')
    module.application = definition
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.chdir(tmp_path)
    assert definition.compile().primary_agent.name == "assistant"
    app = definition.build(
        'desktop_example:application', BuildContext(data_root=tmp_path / 'data'),
        security=Security(), handshake=refuse_socket,
        session_factory=lambda supervisor, context: FakeRuntime(),
    )
    surface = definition.plan.surfaces[0]
    assert surface.source == source
    assert surface.name == 'main' and surface.routes == ('/ui',)
    with TestClient(app) as client:
        assert client.get('/ui/').text == '<h1>My application</h1>'
        assert client.get('/api/v1/session').status_code == 401
        assert client.get(surface.preview_route + '/').status_code == 401


def test_headless_conversation_does_not_resolve_a_ui_module(tmp_path):
    definition = Application(name='Headless', version='1', surfaces=(),
                             primary_agent=AgentSpec(name='assistant'))
    app = definition.build(
        'unimportable_example:application', BuildContext(data_root=tmp_path),
        security=Security(), handshake=refuse_socket,
    )
    assert definition.surface_service is None
    assert not any(route.path == '/ui' for route in app.routes)


def test_default_ui_reports_missing_source_without_silently_becoming_headless(tmp_path, monkeypatch):
    import sys
    from types import ModuleType

    module = ModuleType('missing_ui_example')
    module.__file__ = str(tmp_path / 'application.py')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    definition = Application(name='Desktop', version='1', primary_agent=AgentSpec(name='assistant'))
    with pytest.raises(ValueError, match='surfaces='):
        definition.build(
            'missing_ui_example:application', BuildContext(data_root=tmp_path / 'data'),
            security=Security(), handshake=refuse_socket,
        )
