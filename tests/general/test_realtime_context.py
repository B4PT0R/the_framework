"""Live context policies share transport barriers and never become history."""

import asyncio
from types import SimpleNamespace

import pytest

from tests.fixtures.realtime import Sideband
from the_framework.plugins.realtime.controller import RealtimeController


def controller():
    sent = []
    async def send(request):
        sent.append(request)
    async def publish(event):
        pass
    voice = RealtimeController(SimpleNamespace(
        register_observer=lambda observer: None,
        supervisor=SimpleNamespace(send=send, sent=sent), publish=publish,
    ))
    voice.call_id = "call"
    voice.sideband = Sideband()
    return voice


def texts(voice):
    return [event["content"][0]["text"] for event in voice.sideband.sent]


def test_voice_delegation_uses_low_reasoning_without_disabling_it():
    async def run():
        voice = controller()
        await voice._start_delegation({"item": {
            "type": "delegation", "target": "client", "id": "question",
            "content": [{"type": "input_text", "text": "Bonjour"}],
        }})
        request, = voice.runtime.supervisor.sent
        assert request.prompt == "Bonjour"
        assert request.reasoning_effort == "low"
        assert request.response_kind == "realtime_delegation"
        assert request.id == "realtime-delegation:call:question"

    asyncio.run(run())


def test_context_preserves_order_and_coalesces_latest_state_until_peer_ready():
    async def run():
        voice = controller()
        assert await voice.append_context("delta 1")
        assert await voice.append_context("old state", key="state", delivery="latest")
        assert await voice.append_context("delta 2")
        assert await voice.append_context("new state", key="state", delivery="latest")
        assert texts(voice) == []
        await voice.ready()
        assert texts(voice) == ["delta 1", "delta 2", "new state"]
        for text in texts(voice):
            # A transport echo of provider context must never be reconciled as
            # a user turn (the runtime deliberately has no canonical writer).
            await voice._handle_sideband_event({
                "type": "turn.done", "turn": {"role": "user", "transcript": text},
            })
        assert voice.runtime.supervisor.sent == []

    asyncio.run(run())


def test_context_waits_for_both_response_and_delegated_speech():
    async def run():
        voice = controller()
        voice.peer_ready = True
        voice.realtime_response_active = True
        voice.pending_delegation_deliveries.add("delegation")
        assert await voice.append_context("held state", key="state", delivery="latest")
        assert not await voice.append_context("expired perception", delivery="discard")
        await voice._handle_sideband_event({"type": "response.done"})
        assert texts(voice) == []
        await voice._handle_sideband_event({
            "type": "turn.done", "turn": {"role": "assistant", "transcript": "Answer"},
        })
        assert texts(voice) == ["held state"]

    asyncio.run(run())

def test_context_invalidation_and_call_close_remove_stale_updates():
    async def run():
        voice = controller()
        assert await voice.append_context("old frame", key="frame", delivery="latest")
        voice.discard_context("frame")
        await voice.ready()
        assert texts(voice) == []
        voice.realtime_response_active = True
        assert await voice.append_context("previous call")
        await voice._close()
        voice.call_id = "new call"
        voice.sideband = Sideband()
        await voice.ready()
        assert texts(voice) == []

    asyncio.run(run())


def test_context_queue_pressure_is_reported_without_dropping_accepted_deltas():
    async def run():
        voice = controller()
        for index in range(32):
            assert await voice.append_context(f"delta {index}")
        assert not await voice.append_context("overflow")
        await voice.ready()
        assert texts(voice) == [f"delta {index}" for index in range(32)]
        with pytest.raises(ValueError, match="requires a key"):
            await voice.append_context("state", delivery="latest")

    asyncio.run(run())


def test_updates_during_flush_remain_ordered_and_can_invalidate_queued_state():
    async def run():
        voice = controller()
        entered, release = asyncio.Event(), asyncio.Event()
        original_send = voice._send

        async def send(event):
            entered.set()
            await release.wait()
            return await original_send(event)

        voice._send = send
        await voice.append_context("delta 1")
        await voice.append_context("old state", key="state", delivery="latest")
        await voice.append_context("delta 2")
        flushing = asyncio.create_task(voice.ready())
        await asyncio.wait_for(entered.wait(), 2)
        await voice.append_context("delta 3")
        voice.discard_context("state")
        await voice.append_context("new state", key="state", delivery="latest")
        release.set()
        await asyncio.wait_for(flushing, 2)
        assert texts(voice) == ["delta 1", "delta 2", "delta 3", "new state"]

    asyncio.run(run())


@pytest.mark.parametrize("failed", [False, True])
def test_delegation_preserves_spoken_reply_and_following_voice_turns(failed):
    from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed

    async def run():
        voice = controller()
        await voice._start_delegation({"item": {
            "type": "delegation", "target": "client", "id": "question",
            "content": [{"type": "input_text", "text": "Question"}],
        }})
        request, = voice.runtime.supervisor.sent
        result = (CommandFailed(command=request, error="Simulated failure")
                  if failed else CommandCompleted(command=request))
        await voice.handle_worker_output(result)
        for turn_id, transcript in (("spoken", "Spoken summary"), ("next", "Next reply")):
            before = len(voice.runtime.supervisor.sent)
            await voice._handle_sideband_event({"type": "turn.done", "turn": {
                "id": turn_id, "role": "assistant", "transcript": transcript,
            }})
            assert len(voice.runtime.supervisor.sent) == before + 1
            accepted = voice.runtime.supervisor.sent[-1]
            assert accepted.name == "realtime_turn"
            assert accepted.payload["transcript"] == transcript
            assert accepted.payload["turn_id"] == turn_id
        assert not voice.pending_delegation_deliveries

    asyncio.run(run())


def test_voice_completion_links_pending_display_to_later_canonical_commit():
    from the_framework.agent import Agent
    from the_framework.agent.runtime.worker import Worker
    from the_framework.plugins.realtime.live_events import LiveEvents

    async def run():
        voice = controller()
        event = {"type": "turn.done", "turn": {
            "id": "spoken", "role": "assistant", "transcript": "Keep this phrase visible",
        }}
        message_id = await voice._handle_sideband_event(event)
        completion, = LiveEvents().convert(event, message_id=message_id)
        request, = voice.runtime.supervisor.sent
        assert completion.message_id == request.id == "realtime:call:spoken:assistant"
        agent = Agent(client=object())
        assert not agent.session.history
        worker = Worker(agent, None, None)
        outputs = [output async for output in worker.execute(request)]
        added, = [output for output in outputs if output.type == "agent.response_item.added"]
        assert added.item.id == completion.message_id
        assert added.item.kind == "realtime"
        assert added.item.content[0].text == completion.transcript

    asyncio.run(run())


@pytest.mark.parametrize("delegated", [False, True])
def test_compaction_status_reaches_voice_during_work_without_speech_or_history(delegated):
    from the_framework.agent.runtime.protocol import CommandEvent

    async def run():
        voice = controller()
        voice.peer_ready = True
        voice.realtime_response_active = True
        if delegated:
            voice.delegations["working"] = {"id": "delegation"}
        for phase in ("start", "end"):
            await voice.handle_worker_output(CommandEvent(
                command_id="working", event={"type": f"agent.compaction.{phase}"},
            ))
        assert len(texts(voice)) == 2
        assert "is compacting" in texts(voice)[0]
        assert "has finished compacting" in texts(voice)[1]
        assert "does not mean its current task is complete" in texts(voice)[1]
        assert all(event["type"] == "session.context.append" for event in voice.sideband.sent)
        assert voice.runtime.supervisor.sent == []
        assert not voice.pending_provider_contexts
        assert voice.compaction_command_id is None
        for text in texts(voice):
            await voice._handle_sideband_event({
                "type": "turn.done", "turn": {"role": "user", "transcript": text},
            })
        assert voice.runtime.supervisor.sent == []

    asyncio.run(run())


def test_compaction_failure_does_not_leave_voice_waiting_or_claim_success():
    from the_framework.agent.runtime.protocol import CommandEvent, CommandFailed, PromptRequest

    async def run():
        voice = controller()
        await voice.handle_worker_output(CommandEvent(
            command_id="working", event={"type": "agent.compaction.start"},
        ))
        await voice.handle_worker_output(CommandFailed(
            command=PromptRequest(id="other", prompt="unrelated"), error="failure",
        ))
        assert len(texts(voice)) == 1
        await voice.handle_worker_output(CommandFailed(
            command=PromptRequest(id="working", prompt="work"), error="failure",
        ))
        assert "no longer running" in texts(voice)[-1]
        assert "do not claim that it succeeded" in texts(voice)[-1]
        assert voice.compaction_command_id is None
        assert voice.runtime.supervisor.sent == []

    asyncio.run(run())


def test_compaction_events_do_not_open_a_voice_call():
    from the_framework.agent.runtime.protocol import CommandEvent

    async def run():
        voice = controller()
        sideband = voice.sideband
        voice.sideband = None
        voice.call_id = None
        await voice.handle_worker_output(CommandEvent(
            command_id="working", event={"type": "agent.compaction.start"},
        ))
        assert not sideband.sent
        assert not voice.pending_provider_contexts
        assert voice.call_id is None
        assert voice.runtime.supervisor.sent == []

    asyncio.run(run())
