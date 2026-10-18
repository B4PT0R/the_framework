"""Voice-mode boundaries are canonical developer context, not generated replies."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from the_framework.agent import Agent, Session
from the_framework.agent.context.builder import Context
from the_framework.agent.context.projections import SessionProjections
from the_framework.agent.runtime.protocol import ExternalEventRequest, ShutdownRequest
from the_framework.agent.runtime.worker import Worker
from the_framework.plugins.realtime.agent import RealtimeExtension
from the_framework.plugins.realtime.controller import RealtimeController
from the_framework.plugins.realtime.mode import voice_mode_marker
from tests.fixtures.realtime import Sideband


def test_voice_snapshot_reserves_capacity_for_the_current_start_marker():
    agent = Agent(client=object())
    extension = agent.add_extension(RealtimeExtension)
    extension.config.max_initial_items = 2
    agent.add_message("user", "Question")
    agent.add_message("assistant", "Answer")
    snapshot = extension.session_snapshot({"items": list(agent.session.history), "extensions": {}})
    initial = snapshot["extensions"]["realtime"]["session"]["initial_items"]
    assert len(initial) == 1
    assert initial[0]["content"][0]["text"] == "Answer"


def test_mode_markers_survive_replay_and_binding_removal_without_inference(tmp_path):
    agent = Agent(client=object(), session_path=tmp_path / "session.json")
    extension = agent.add_extension(RealtimeExtension)
    agent.deactivate_extension(extension)

    async def run():
        incoming, outgoing = asyncio.Queue(), []

        async def send(output):
            outgoing.append(output)

        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        async with asyncio.timeout(10):
            for count, state in enumerate(("started", "ended", "ended"), 1):
                marker = voice_mode_marker("call", state)
                command = ExternalEventRequest.from_dict(json.loads(json.dumps(ExternalEventRequest(
                    id=marker.id, name="realtime_mode", input_items=[marker],
                ))))
                await incoming.put(command)
                while sum(item.type == "command_completed" for item in outgoing) < count:
                    await asyncio.sleep(0)
            await incoming.put(ShutdownRequest(id="stop"))
            await running

    asyncio.run(run())
    history = Session.open(agent.session.path).history
    assert [item.voice_state for item in history] == ["started", "ended"]
    assert all(item.role == "developer" and item.kind == "realtime_mode" for item in history)
    assert SessionProjections().project("display", history) == []
    projected = Context.project(history)
    assert all(item["role"] == "developer" for item in projected)
    assert "Voice session started" in projected[0]["content"][0]["text"]
    snapshot = extension.session_snapshot({"items": history, "extensions": {}})
    initial = snapshot["extensions"]["realtime"]["session"]["initial_items"]
    assert initial == projected


@pytest.mark.parametrize("explicit_stop", [False, True])
def test_successful_call_projects_start_and_queues_one_end_after_cleanup(explicit_stop):
    async def run():
        sent, sessions, barriers = [], [], []
        sideband = Sideband()

        def create(**kwargs):
            sessions.append(kwargs["session"])
            return SimpleNamespace(session=SimpleNamespace(id="transport"), transport=SimpleNamespace(sdp="answer"))

        manager = SimpleNamespace(enter=lambda: sideband, __exit__=lambda *args: None)
        client = SimpleNamespace(live=SimpleNamespace(
            create=create, sideband=SimpleNamespace(connect=lambda **kwargs: manager),
        ))

        async def send(request):
            sent.append(request)

        voice = RealtimeController(SimpleNamespace(
            register_observer=lambda observer: None, supervisor=SimpleNamespace(send=send),
        ), client_factory=lambda: client)

        async def reader():
            pass

        async def barrier(call_id, **kwargs):
            assert sent[-1].input_items[0].voice_state == "ended"
            barriers.append(call_id)

        voice._read_sideband = reader
        voice._worker_barrier = barrier
        source_session = {"model": "test-model"}
        snapshot = SimpleNamespace(watermark=None, extensions={"realtime": {
            "protocol": "codex-realtime-v3", "model": "test-model",
            "session": source_session, "providers": [],
        }})
        await voice.start("offer", snapshot)
        assert len(sent) == 1
        start = sent[0].input_items[0]
        assert sessions[0]["initial_items"] == Context.project([start])
        assert "initial_items" not in source_session
        if explicit_stop:
            await voice.stop()
            assert barriers == ["transport"]
        else:
            await voice._close()
        await voice._close()
        assert [request.input_items[0].voice_state for request in sent] == ["started", "ended"]
        assert sent[0].input_items[0].voice_session_id == sent[1].input_items[0].voice_session_id

    asyncio.run(run())


def test_failed_call_creation_does_not_record_a_voice_session():
    async def run():
        sent = []

        def fail(**kwargs):
            raise RuntimeError("call creation failed")

        async def send(request):
            sent.append(request)

        client = SimpleNamespace(live=SimpleNamespace(create=fail))
        voice = RealtimeController(SimpleNamespace(
            register_observer=lambda observer: None, supervisor=SimpleNamespace(send=send),
        ), client_factory=lambda: client)
        snapshot = SimpleNamespace(extensions={"realtime": {
            "protocol": "codex-realtime-v3", "session": {}, "providers": [],
        }})
        with pytest.raises(RuntimeError, match="call creation failed"):
            await voice.start("offer", snapshot)
        assert sent == [] and not voice.active

    asyncio.run(run())
