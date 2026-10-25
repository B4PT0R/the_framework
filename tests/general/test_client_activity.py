"""Private operations follow accepted participation, never connectivity or replay."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.clients.activity import ActiveClients, client_key, request_client


class Socket:
    def __init__(self):
        self.sent = asyncio.Queue()

    async def send_json(self, payload):
        await self.sent.put(payload)


def test_identity_is_authenticated_and_instance_bounded():
    assert client_key("remote:phone", "page-1") == ("remote:phone", "page-1")
    for value in (None, "", "a" * 101, "a/b", ["bad"]):
        assert client_key("local", value) is None
    call = SimpleNamespace(principal=SimpleNamespace(id="phone"), request=SimpleNamespace(headers={"x-client-instance": "page"}))
    assert request_client(call) == ("phone", "page")
    assert request_client(None) is None


def test_targets_only_last_active_socket_and_authenticates_receipts():
    async def run():
        clients = ActiveClients()
        home, phone = Socket(), Socket()
        home_key, phone_key = ("local", "home"), ("remote:phone", "page")
        clients.connect(home_key, home)
        clients.connect(phone_key, phone)
        assert (await clients.request("audio.play", {}))["status"] == "unavailable"
        await clients.activate(home_key)
        await clients.activate(phone_key)
        assert (await home.sent.get())["type"] == "client_deactivated"
        task = asyncio.create_task(clients.request("audio.play", {"src": "/api/v1/bucket/a.wav"}))
        event = await phone.sent.get()
        assert home.sent.empty()
        clients.receive(home, {"type": "client_operation_result", "id": event["id"], "status": "playing"})
        assert not task.done()
        clients.receive(phone, {"type": "client_operation_result", "id": [], "status": "playing"})
        clients.receive(phone, {"type": "client_operation_result", "id": event["id"], "status": "blocked"})
        result = await task
        assert result["status"] == "blocked" and result["client"]["principal"] == "remote:phone"
        assert not clients.pending
    asyncio.run(run())


def test_disconnect_reconnect_unknown_input_and_timeout_never_fall_back_or_replay():
    async def run():
        clients, home, phone = ActiveClients(), Socket(), Socket()
        clients.connect(("home", "tab"), home)
        clients.connect(("phone", "tab"), phone)
        await clients.activate(("phone", "tab"))
        task = asyncio.create_task(clients.request("audio.play", {}))
        await phone.sent.get()
        clients.disconnect(("phone", "tab"), phone)
        assert (await task)["status"] == "disconnected"
        assert (await clients.request("audio.play", {}))["status"] == "unavailable"
        replacement = Socket()
        clients.connect(("phone", "tab"), replacement)
        assert replacement.sent.empty() and home.sent.empty()
        assert (await clients.request("audio.play", {}, timeout=.001))["status"] == "unconfirmed"
        assert not clients.pending
        await clients.activate(None)
        assert (await clients.request("audio.play", {}))["status"] == "unavailable"
        assert home.sent.empty()
    asyncio.run(run())


def test_handoff_cancels_pending_operation_and_stops_previous_client():
    async def run():
        clients, home, phone = ActiveClients(), Socket(), Socket()
        clients.connect(("home", "tab"), home)
        clients.connect(("phone", "tab"), phone)
        await clients.activate(("home", "tab"))
        task = asyncio.create_task(clients.request("audio.play", {}))
        await home.sent.get()
        await clients.activate(("phone", "tab"))
        assert (await home.sent.get())["type"] == "client_deactivated"
        assert (await task)["status"] == "inactive"
        with pytest.raises(RuntimeError, match="already connected"):
            clients.connect(("phone", "tab"), Socket())
        assert phone.sent.empty()
    asyncio.run(run())


def test_slow_socket_send_is_bounded_and_never_retried():
    async def run():
        class SlowSocket:
            async def send_json(self, payload):
                await asyncio.Event().wait()

        clients = ActiveClients()
        key = ('home', 'page')
        clients.connect(key, SlowSocket())
        await clients.activate(key)
        assert await clients.request('audio.play', {}, timeout=.001) == {'status': 'unconfirmed'}
        assert not clients.pending
    asyncio.run(run())


def test_only_successful_text_input_selects_a_client(tmp_path):
    from the_framework.server.api.chat import ChatApi

    async def run():
        clients = ActiveClients()
        await clients.activate(('home', 'page'))
        call = SimpleNamespace(principal=SimpleNamespace(id='phone'), request=SimpleNamespace(headers={'x-client-instance': 'page'}))

        async def submit(command):
            if command.prompt == 'fail':
                raise RuntimeError('not accepted')

        chat = ChatApi(SimpleNamespace(submit=submit), tmp_path, clients=clients)
        with pytest.raises(RuntimeError, match='not accepted'):
            await chat.prompt('one', 'fail', call=call)
        assert clients.active == ('home', 'page')
        await chat.prompt('two', 'hello', call=call)
        assert clients.active == ('phone', 'page')
        await chat.prompt('three', 'unknown client')
        assert clients.active is None
    asyncio.run(run())


@pytest.mark.parametrize('resume,fail', [(False, False), (True, False), (False, True)])
def test_voice_start_selects_only_successful_explicit_interaction(resume, fail):
    from the_framework.plugins.realtime.api import RealtimeApi
    from the_framework.server.api.endpoints import HttpError
    from the_framework.agent.runtime.protocol import StatusRequest

    async def run():
        clients = ActiveClients()
        await clients.activate(('home', 'page'))
        call = SimpleNamespace(principal=SimpleNamespace(id='phone'), request=SimpleNamespace(headers={'x-client-instance': 'page'}))

        async def request(command):
            return SimpleNamespace(foreground_active=None, foreground_pending=0) if isinstance(command, StatusRequest) else object()

        async def start(*args, **kwargs):
            if fail:
                raise RuntimeError('failed')
            return {'answer_sdp': 'answer'}

        voice = RealtimeApi(SimpleNamespace(request=request), SimpleNamespace(active=False, start=start), transport=SimpleNamespace(clients=clients))
        if fail:
            with pytest.raises(HttpError):
                await voice.start('offer', 'call', call=call)
        else:
            await voice.start('offer', 'call', resume_call_id='old' if resume else None, call=call)
        assert clients.active == (('home', 'page') if fail or resume else ('phone', 'page'))
    asyncio.run(run())
