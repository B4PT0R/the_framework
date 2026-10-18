"""Preview capabilities are private, read-only and never replace the desktop."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import starter.server
from tests.fixtures.runtime import FakeRuntime
from the_framework import Application, ClientSurface


def application(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'dist').mkdir()
    (source / 'dist/index.html').write_text('<html><head><title>Candidate</title></head><body>Preview</body></html>')
    surface = ClientSurface(name='mobile', source=source, artifact='dist', build=('true',), routes=('/mobile',))
    monkeypatch.setattr(starter.server, 'application', Application({
        **starter.server.application, 'surfaces': (surface,),
    }))
    runtime = FakeRuntime()

    async def stream():
        while True:
            yield await asyncio.Queue().get()

    runtime.stream = stream
    app = starter.server.create_app(tmp_path, token='control', browser_token='principal',
                                    origin='http://testserver', runtime=runtime)
    return app, runtime


def test_preview_is_authenticated_read_only_and_does_not_take_primary_connections(tmp_path, monkeypatch):
    app, runtime = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set('starter_session', 'principal')
        with client.websocket_connect('/api/v1/events', headers={'origin': 'http://testserver'}) as main:
            assert main is not None
            with client.websocket_connect('/api/v1/application', headers={'origin': 'http://testserver'}):
                with (
                    pytest.raises(WebSocketDisconnect) as duplicate,
                    client.websocket_connect('/api/v1/events', headers={'origin': 'http://testserver'}),
                ):
                    pytest.fail('a second principal was accepted')
                assert duplicate.value.code == 1013
                response = client.post('/api/v1/surfaces/mobile/preview')
                assert response.status_code == 200, response.text
                link = response.json()['url']
                client.cookies.clear()
                assert client.get('/__surface-preview/mobile/' + app.state.application.surface_service.active_path('mobile').name + '/').status_code == 401
                opened = client.get(link)
                assert opened.status_code == 200, opened.text
                assert 'framework-client-role' in opened.text
                assert client.get(link, follow_redirects=False).status_code == 401
                assert client.get('/api/v1/surfaces').status_code == 200
                assert client.post('/api/v1/agent/prompt', json={'prompt': 'Do not submit'}).status_code in {401, 403}
                assert client.patch('/api/v1/config', json={'model': 'do-not-save'}).status_code in {401, 403}
                assert client.post('/api/v1/surfaces/mobile/publish').status_code in {401, 403}
                with (
                    pytest.raises(WebSocketDisconnect) as denied,
                    client.websocket_connect('/api/v1/application', headers={'origin': 'http://testserver'}),
                ):
                    pytest.fail('preview acquired the application bridge')
                assert denied.value.code == 1008
                with client.websocket_connect('/api/v1/events', headers={'origin': 'http://testserver'}):
                    transport = app.state.application.default_session.services['canonical_transport']
                    assert transport.event_connection_count == 2
                    assert transport.primary_event_connection_count == 1
                    assert runtime.bridge.connected
                assert runtime.bridge.connected
                assert runtime.submitted == []


def test_preview_cookie_survives_reconstruction_and_cannot_fall_back_to_primary(tmp_path, monkeypatch):
    app, _ = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set('starter_session', 'principal')
        link = client.post('/api/v1/surfaces/mobile/preview').json()['url']
        client.cookies.clear()
        assert client.get(link).status_code == 200
        cookie_name = app.state.application.surface_service.previews.cookie
        cookie = client.cookies.get(cookie_name)
    restored = starter.server.create_app(tmp_path, token='new-control', browser_token='principal',
                                         origin='http://testserver', runtime=FakeRuntime())
    with TestClient(restored) as client:
        client.cookies.set(cookie_name, cookie)
        assert client.get('/api/v1/surfaces').status_code == 200
        client.cookies.set(cookie_name, cookie + 'tampered')
        client.cookies.set('starter_session', 'principal')
        assert client.get('/api/v1/surfaces').status_code == 401


def test_preview_ticket_rejects_expiry_wrong_surface_and_foreign_origin(tmp_path, monkeypatch):
    app, _ = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set('starter_session', 'principal')
        link = client.post('/api/v1/surfaces/mobile/preview').json()['url']
        previews = app.state.application.surface_service.previews
        ticket = link.rsplit('/', 1)[-1]
        previews.tickets[ticket] = (0, 'mobile', previews.service.active_path('mobile').name)
        assert client.get(link, follow_redirects=False).status_code == 401


        fresh = client.post('/api/v1/surfaces/mobile/preview').json()['url']
        assert client.get(fresh, headers={'origin': 'https://attacker.invalid'}, follow_redirects=False).status_code == 403
        assert client.get(fresh).status_code == 200


def test_agent_preview_control_opens_a_new_authenticated_web_tab(tmp_path, monkeypatch):
    application(tmp_path, monkeypatch)
    app = starter.server.create_app(tmp_path, token='control', browser_token='principal', origin='http://testserver')
    calls = []
    class Browser:
        def __init__(self, path):
            self.path = path
        def call(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
    monkeypatch.setattr('the_framework.server.composition.controls.BrowserRPCClient', Browser)
    with TestClient(app) as client:
        runtime = app.state.application.default_session.services['runtime']
        before_session = client.get('/api/v1/session', headers={'authorization': 'Bearer control'}).json()['watermark']
        before = app.state.application.surface_service.active_path('mobile').name
        result = client.portal.call(runtime.bridge.call, 'surfaces', 'preview', {'name': 'mobile'})
        assert result == {'surface': 'mobile', 'release': before, 'target': 'web', 'role': 'preview'}
        assert len(calls) == 1
        method, url, options = calls[0]
        assert method == 'open_web' and options == {'new_tab': True}
        assert client.get(url).status_code == 200
        assert client.get('/api/v1/surfaces').status_code == 200
        assert app.state.application.surface_service.active_path('mobile').name == before
        assert client.get('/api/v1/session').json()['watermark'] == before_session
