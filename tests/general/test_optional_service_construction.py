"""Unavailable optional resources degrade; invalid declarations still fail."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from the_framework import Application, AgentSpec, BuildContext, Plugin, ServerExtension
from the_framework.server.api.endpoints import Principal
from tests.fixtures.multi_session_app import application as agents


class Security:
    async def authenticate(self, request):
        if request.headers.get('authorization') == 'Bearer test':
            return Principal(id='test', scopes=frozenset({'*'}))
    async def authorize(self, principal, requirement, request):
        return True


async def handshake(socket):
    if socket.headers.get('authorization') == 'Bearer test':
        return {'subprotocol': None}


def test_optional_construction_failure_is_visible_and_chat_and_other_sessions_survive(tmp_path):
    calls = []
    def weather():
        calls.append('weather')
        raise OSError('weather service offline')
    def view(weather):
        calls.append('view')
        raise AssertionError('a dependent factory must not run without its dependency')
    plugin = Plugin(name='weather', server_extension=(
        ServerExtension(name='weather', service_factory=weather, critical=False, endpoints='service'),
        ServerExtension(name='weather_view', service_factory=view, critical=False, scope='session', endpoints='service'),
    ))
    declaration = Application({**agents, 'plugins': (*agents.plugins, plugin)})
    app = declaration.build('tests.fixtures.multi_session_app:application',
        BuildContext(data_root=tmp_path), security=Security(), handshake=handshake, session_projection='display')
    assert calls == ['weather']
    with TestClient(app) as client:
        client.headers['authorization'] = 'Bearer test'
        assert client.get('/api/v1/session').status_code == 200
        root = app.state.application
        client.portal.call(root.primary_session.apply_event, 'record', {'text': 'Chat survives'})
        assert 'Chat survives' in client.get('/api/v1/session').text
        child = client.post('/api/v1/sessions', json={}).json()['id']
        for prefix in ('', f'/sessions/{child}'):
            health = client.get(prefix+'/api/v1/health').json()
            assert health['status'] == 'ok'
            assert health['services']['weather']['status'] == 'error'
            assert health['services']['weather']['details'] == {'status': 'error', 'phase': 'construction', 'error': 'weather service offline'}
            assert health['services']['weather_view']['status'] == 'error'
            plugins = client.get(prefix+'/api/v1/plugins').json()['plugins']
            assert next(p for p in plugins if p['name']=='weather')['running'] is False
            assert client.get(prefix+'/api/v1/weather').status_code == 404
        assert calls == ['weather']
        assert 'weather' not in root.services
        assert 'weather_view' not in root.services


def test_new_composition_retries_optional_construction_after_resource_recovers():
    attempts = []
    class Service:
        async def start(self):
            attempts.append('start')
        async def stop(self):
            attempts.append('stop')
    def factory():
        attempts.append('construct')
        if len(attempts)==1:
            raise OSError('offline')
        return Service()
    spec = Application(name='Example', version='1', primary_agent=AgentSpec(name='agent'),
                           server_extensions=(ServerExtension(name='resource', critical=False, service_factory=factory),))
    async def run():
        unavailable = spec._build_server()
        async with unavailable.router.lifespan_context(unavailable):
            assert (await unavailable.state.application.service_graph.health())['resource']['status']=='error'
        recovered = spec._build_server()
        async with recovered.router.lifespan_context(recovered):
            assert (await recovered.state.application.service_graph.health())['resource']['status']=='running'
    asyncio.run(run())
    assert attempts == ['construct', 'construct', 'start', 'stop']


@pytest.mark.parametrize('dependent', [False, True])
def test_required_resource_failure_remains_fatal(dependent):
    def fail():
        raise OSError('offline')
    extensions = [ServerExtension(name='resource', service_factory=fail, critical=not dependent)]
    if dependent:
        extensions.append(ServerExtension(name='required_consumer', service_factory=lambda resource: object()))
    with pytest.raises((OSError, RuntimeError), match='offline|dependencies are unavailable'):
        Application(name='Example', version='1', primary_agent=AgentSpec(name='agent'), server_extensions=extensions)._build_server()


@pytest.mark.parametrize('failure', ['missing_dependency', 'automatic_endpoints', 'async_factory'])
def test_invalid_optional_service_contract_is_not_downgraded(failure):
    async def asynchronous():
        return object()
    if failure=='missing_dependency':
        extension = ServerExtension(name='invalid', critical=False, service_factory=lambda missing: object())
    elif failure=='automatic_endpoints':
        extension = ServerExtension(name='invalid', critical=False, service_factory=lambda: object(), endpoints='service')
    else:
        extension = ServerExtension(name='invalid', critical=False, service_factory=asynchronous)
    with pytest.raises((ValueError, TypeError)):
        Application(name='Example', version='1', primary_agent=AgentSpec(name='agent'), server_extensions=(extension,))._build_server()
