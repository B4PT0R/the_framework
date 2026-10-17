"""All client transports share the canonical text/voice submission barrier."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.agent.runtime.protocol import PromptRequest, StatusRequest
from the_framework.server.runtime.ingress import ChatIngress
from the_framework.server.api.chat import ChatApi
from the_framework.plugins.realtime.api import RealtimeApi


def test_text_ingress_preserves_the_complete_command():
    async def run():
        submitted = []

        async def submit(command):
            submitted.append(command)

        ingress = ChatIngress(SimpleNamespace(submit=submit))
        command = PromptRequest(id="custom", prompt="", append_prompt=False,
                                prompt_role="developer", reasoning_effort="low")
        assert await ingress.submit(command) == {"id": "custom", "route": "text"}
        assert submitted == [command] and submitted[0] is command

    asyncio.run(run())


@pytest.mark.parametrize("transition", ["start", "stop"])
def test_submission_waits_for_voice_transition_before_selecting_route(tmp_path, transition):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        submitted, spoken = [], []

        async def request(command):
            if isinstance(command, StatusRequest):
                return SimpleNamespace(foreground_active=None, foreground_pending=0)
            return object()

        async def submit(command):
            submitted.append(command)

        async def start(offer, snapshot):
            entered.set()
            await release.wait()
            controller.active = True
            return {"answer_sdp": "answer"}

        async def stop():
            entered.set()
            await release.wait()
            controller.active = False

        async def send_text(text):
            spoken.append(text)
            return "voice-turn"

        runtime = SimpleNamespace(submit=submit, request=request)
        controller = SimpleNamespace(active=transition == "stop", start=start,
                                     stop=stop, send_text=send_text)
        voice = RealtimeApi(runtime, controller)
        chat = ChatApi(runtime, tmp_path, voice)
        changing = asyncio.create_task(voice.start("offer", "call") if transition == "start" else voice.stop())
        await entered.wait()
        pending = asyncio.create_task(chat.ingress.submit(PromptRequest(id="remote", prompt="Remote")))
        local = asyncio.create_task(chat.prompt("local", "Local"))
        await asyncio.sleep(0)
        assert not pending.done() and not local.done()
        assert submitted == spoken == []
        release.set()
        await changing
        result, local_result = await asyncio.gather(pending, local)
        assert local_result == {"id": "local", "status": "submitted"}
        if transition == "start":
            assert result == {"id": "voice-turn", "route": "realtime"}
            assert spoken == ["Remote", "Local"] and submitted == []
        else:
            assert result == {"id": "remote", "route": "text"}
            assert [command.id for command in submitted] == ["remote", "local"] and spoken == []

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("fields", [
    {"append_prompt": False},
    {"input_items": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "attachment"}]}]},
    {"prompt_role": "developer"},
    {"prompt_kind": "custom"},
    {"reasoning_effort": "low"},
])
def test_voice_rejects_turns_whose_semantics_cannot_be_preserved(fields):
    async def run():
        async def unexpected(*args):
            pytest.fail("A rejected turn must not be submitted")

        voice = SimpleNamespace(lock=asyncio.Lock(), controller=SimpleNamespace(active=True, send_text=unexpected))
        ingress = ChatIngress(SimpleNamespace(submit=unexpected), voice)
        with pytest.raises(ValueError, match="requires text mode"):
            await ingress.submit(PromptRequest(id="structured", prompt="Hello", **fields))
        assert not voice.lock.locked()

    asyncio.run(run())


def test_failed_or_cancelled_submission_releases_the_barrier():
    async def run():
        entered = asyncio.Event()

        async def send_text(text):
            if text == "fail":
                raise RuntimeError("voice not ready")
            entered.set()
            await asyncio.Future()

        voice = SimpleNamespace(lock=asyncio.Lock(), controller=SimpleNamespace(active=True, send_text=send_text))
        ingress = ChatIngress(None, voice)
        with pytest.raises(ValueError, match="voice not ready"):
            await ingress.submit(PromptRequest(id="failed", prompt="fail"))
        assert not voice.lock.locked()
        pending = asyncio.create_task(ingress.submit(PromptRequest(id="cancelled", prompt="wait")))
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not voice.lock.locked()

    asyncio.run(asyncio.wait_for(run(), 5))
