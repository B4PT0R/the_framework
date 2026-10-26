"""Voice transport replacement preserves canonical turn boundaries and intent."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.plugins.realtime.api import RealtimeApi
from the_framework.plugins.realtime.events import RealtimeRestarting
from the_framework.server.api.endpoints import HttpError
from the_framework.server.runtime.session import SessionRuntime


@pytest.mark.parametrize("transition", ["restart", "publication"])
def test_voice_finishes_and_drains_before_transport_replacement(transition):
    async def run():
        calls = []
        completed = asyncio.Event()
        async def finish(**kwargs):
            calls.extend(["input_audio.pause", "wait"])
            await completed.wait()
            calls.append("persisted")
        async def publish(event):
            assert isinstance(event, RealtimeRestarting)
            assert event.call_id == "owning-tab"
            calls.append("client-notified")
        controller = SimpleNamespace(active=True, finish_call=finish)
        voice = RealtimeApi(SimpleNamespace(publish=publish), controller)
        voice.call_id = "owning-tab"
        if transition == "restart":
            task = asyncio.create_task(voice.prepare_restart({"resume_route": "realtime_delegation"}))
        else:
            assert await voice.suspend("another-tab") == {"status": "ready"}
            assert calls == []
            task = asyncio.create_task(voice.suspend("owning-tab"))
        await asyncio.sleep(0)
        assert calls == ["input_audio.pause", "wait"]
        completed.set()
        await task
        assert calls[:3] == ["input_audio.pause", "wait", "persisted"]
        assert voice.call_id is None
        if transition == "restart":
            assert calls[-1] == "client-notified"
            with pytest.raises(HttpError) as caught:
                await voice.start("offer", "new-call")
            assert caught.value.status_code == 503
        else:
            assert not voice.restarting
    asyncio.run(run())


def test_restart_prepares_every_live_session_before_process_exit():
    async def run():
        calls = []
        async def restart(intent):
            calls.append("process-exit")
        runtimes = [SessionRuntime(object(), restart=restart) for _ in range(2)]
        for index, runtime in enumerate(runtimes):
            async def prepare(intent, index=index):
                calls.append(("voice", index))
            async def barrier(name, payload, *, timeout, index=index):
                assert name == "server_restart_barrier" and timeout is None
                calls.append(("worker-drained", index))
            runtime.register_restart_hook(prepare)
            runtime.apply_event = barrier
        runtimes[0].application = SimpleNamespace(session_apps={
            str(index): (SimpleNamespace(state=SimpleNamespace(session=runtime)), object())
            for index, runtime in enumerate(runtimes)
        })
        await runtimes[0].restart_server({})
        assert calls == [("voice", 0), ("worker-drained", 0),
                         ("voice", 1), ("worker-drained", 1), "process-exit"]
    asyncio.run(run())


@pytest.mark.parametrize("completes", [True, False])
def test_controller_preserves_an_unfinished_voice_turn(completes):
    from the_framework.plugins.realtime.controller import RealtimeController
    from tests.fixtures.realtime import Sideband

    async def run():
        voice = RealtimeController(SimpleNamespace(register_observer=lambda hook: None))
        voice.call_id, voice.sideband = "call", Sideband()
        voice.realtime_response_active = True
        sideband = voice.sideband
        stopped = []
        async def wait(**kwargs):
            assert kwargs["activation_timeout"] == 0
            voice.realtime_response_active = not completes
        async def stop(**kwargs):
            assert kwargs == {"barrier_timeout": None}
            stopped.append(True)
        voice.wait_for_voice_turn_completion, voice.stop = wait, stop
        if completes:
            await voice.finish_call()
            assert stopped == [True]
        else:
            with pytest.raises(TimeoutError):
                await voice.finish_call()
            assert stopped == []
        assert sideband.sent[0] == {"type": "input_audio.pause"}
        if not completes:
            assert sideband.sent[-1] == {"type": "input_audio.resume"}
    asyncio.run(run())


def test_voice_drain_waits_for_delegated_results_before_the_spoken_reply():
    from the_framework.plugins.realtime.controller import RealtimeController
    from tests.fixtures.realtime import Sideband

    async def run():
        voice = RealtimeController(SimpleNamespace(register_observer=lambda hook: None))
        voice.call_id, voice.sideband = "call", Sideband()
        voice.delegations["working"] = object()
        calls = []
        async def barrier(call_id, *, timeout):
            assert timeout is None
            calls.append("delegation-finished")
            voice.pending_delegation_deliveries.add("spoken-result")
        async def wait(**kwargs):
            assert kwargs["activation_timeout"] == 5
            calls.append("spoken-result-finished")
            voice.pending_delegation_deliveries.clear()
        async def stop(**kwargs):
            calls.append("canonical-drained")
        voice._worker_barrier, voice.wait_for_voice_turn_completion, voice.stop = barrier, wait, stop
        await voice.finish_call()
        assert calls == ["delegation-finished", "spoken-result-finished", "canonical-drained"]
    asyncio.run(run())


@pytest.mark.parametrize("response_active", [True, False])
def test_publication_waits_past_response_done_until_delegated_speech_finishes(response_active):
    from tests.general.test_realtime_context import controller

    async def run():
        voice = controller()
        voice.realtime_response_active = response_active
        voice.pending_delegation_deliveries.add("result")
        voice.restart_resume_input_paused = True
        stopped = asyncio.Event()
        async def stop(**kwargs):
            stopped.set()
        voice.stop = stop
        task = asyncio.create_task(voice.finish_call())
        try:
            await asyncio.sleep(0.01)
            assert not task.done()
            await voice._handle_sideband_event({"type": "response.done"})
            await asyncio.sleep(0.01)
            assert not task.done()
            assert not stopped.is_set()
            await voice._handle_sideband_event({"type": "turn.done", "turn": {
                "role": "assistant", "transcript": "Publication terminée.",
            }})
            await asyncio.wait_for(task, timeout=1)
            assert stopped.is_set()
            assert not voice.transport_draining
            assert {"type": "input_audio.resume"} not in voice.sideband.sent
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("owner", ["owning-tab", "another-tab"])
def test_restart_context_only_reaches_ready_replacement_of_owning_call(owner):
    from unittest.mock import AsyncMock
    from the_framework.plugins.realtime.controller import RealtimeController
    from tests.fixtures.realtime import Sideband

    async def run():
        controller = RealtimeController(SimpleNamespace(register_observer=lambda hook: None))
        controller.call_id, controller.sideband = "new-backend-call", Sideband()
        controller.resume_call_id = owner
        controller.ready_context = AsyncMock()
        controller.flush_context = AsyncMock()
        await controller.queue_restart_resume({
            "action_id": "restart", "resume_instruction": "Continue the authorized work",
            "resume_call_id": "owning-tab",
        })
        assert controller.sideband.sent == []  # Sideband alone is not a ready peer.
        result = await controller.ready()
        expected = owner == "owning-tab"
        assert result["resume_injected"] is expected
        injected = [e for e in controller.sideband.sent if e["type"] == "session.context.append"]
        assert bool(injected) is expected
        assert controller.restart_resume_input_paused is expected
        await controller.ready()
        assert len([e for e in controller.sideband.sent if e["type"] == "session.context.append"]) == int(expected)
        await controller.cancel_restart_resume("restart")
        assert not controller.restart_resume_input_paused
        if expected:
            await controller._start_delegation({"item": {
                "type": "delegation", "target": "client", "id": "late",
                "content": [{"type": "input_text", "text": "Resume work"}],
            }})
            assert not controller.delegations
        assert not controller.pending_restart_resumes
    asyncio.run(run())
