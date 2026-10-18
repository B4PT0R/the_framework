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
