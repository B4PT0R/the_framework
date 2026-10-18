"""Public Application.build: reusable definitions, explicit owners and startup."""
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


def test_service_only_definition_builds_independent_hosts_and_starts_only_in_lifespan():
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
    first, second = definition.build(), definition.build()
    a, b = first.state.application, second.state.application
    assert a is not b and a.definition is b.definition is definition
    assert a.primary_session is b.primary_session is None
    assert not a.services.view and not b.services.view and events == []
    assert a.server_extensions['resource'].service is not b.server_extensions['resource'].service
    assert all(route.path not in ('/api/v1/agent/prompt', '/api/v1/sessions') for route in first.routes)
    with TestClient(first), TestClient(second):
        assert a.service('resource') is not b.service('resource')
        assert [event for event, _ in events] == ['start', 'start']
    assert [event for event, _ in events] == ['start', 'start', 'stop', 'stop']
    assert not a.services.view and not b.services.view


def test_agent_definition_builds_independent_session_controllers_without_starting_workers(tmp_path):
    contexts = [BuildContext(data_root=tmp_path/name) for name in ('first', 'second')]
    servers = [application.build('tests.fixtures.multi_session_app:application', context,
                                security=Security(), handshake=refuse_socket) for context in contexts]
    a, b = [server.state.application for server in servers]
    assert a.definition is b.definition is application
    assert isinstance(a.primary_session, SessionRuntime)
    assert a.primary_session is not b.primary_session
    assert a.primary_session.bridge is not b.primary_session.bridge
    assert not a.primary_session.supervisor.running and not b.primary_session.supervisor.running
    assert a.primary_session.supervisor.session_path != b.primary_session.supervisor.session_path
    assert not any(context.services for context in contexts)
    assert all(any(route.path == '/api/v1/agent/prompt' for route in server.routes) for server in servers)


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
