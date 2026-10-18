"""Agent-requested voice controls respect canonical and audible turn boundaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from the_framework.agent import Agent
from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from the_framework.plugins.realtime.agent import RealtimeExtension
from the_framework.plugins.realtime.api import RealtimeApi


def completion(command_id="turn", *, failed=False):
    command = PromptRequest(id=command_id, prompt="voice control", status="completed")
    return (CommandFailed(command=command, error="failed") if failed else
            CommandCompleted(command=command))


def api(*, active=False):
    runtime = SimpleNamespace(publish=AsyncMock())
    controller = SimpleNamespace(active=active, finish_call=AsyncMock(), stop=AsyncMock())
    transport = SimpleNamespace(primary_event_connection_count=1, send_primary=AsyncMock())
    result = RealtimeApi(runtime, controller, transport)
    result.call_id = "owning-tab" if active else None
    return result


def test_tools_use_realtime_capability_and_return_without_waiting_for_audio():
    async def run():
        agent = Agent(client=object())
        agent.bridge = SimpleNamespace(call=AsyncMock(return_value={"status": "voice_control_requested"}))
        extension = agent.add_extension(RealtimeExtension)
        for method, tool in (("start", extension.start_session), ("stop", extension.stop_session),
                             ("status", extension.session_status)):
            await tool()
            agent.bridge.call.assert_awaited_with("realtime", method, {}, timeout_ms=10_000)
    asyncio.run(run())


def test_start_waits_for_originating_command_and_targets_only_primary_interface():
    async def run():
        voice = api()
        result = await voice.handle("start", {}, command_id="turn")
        assert result["status"] == "voice_control_requested"
        voice.transport.send_primary.assert_not_awaited()
        voice.observe(completion("other-turn"))
        assert voice.control_task is None
        voice.observe(completion())
        task = voice.control_task
        assert task is not None
        await task
        voice.transport.send_primary.assert_awaited_once()
        assert json.loads(json.dumps(voice.transport.send_primary.await_args.args[0])) == {
            "type": "realtime_control_requested", "action": "start", "call_id": None}
        voice.runtime.publish.assert_not_awaited()
        assert (await voice.handle("status", {}))["pending"] is None
    asyncio.run(run())


def test_stop_acknowledges_then_waits_for_the_spoken_result_before_notifying_client():
    async def run():
        voice = api(active=True)
        spoken = asyncio.Event()
        draining = asyncio.Event()
        async def finish(**options):
            assert options == {"activation_timeout": 5}
            draining.set()
            await spoken.wait()
        voice.controller.finish_call.side_effect = finish
        command_id = "realtime-delegation:call:delegation"
        result = await voice.handle("stop", {}, command_id=command_id)
        assert result["status"] == "voice_control_requested"
        voice.controller.finish_call.assert_not_awaited()
        assert voice.call_id == "owning-tab"
        voice.observe(completion(command_id))
        task = voice.control_task
        await draining.wait()
        voice.runtime.publish.assert_not_awaited()
        assert voice.call_id == "owning-tab"
        spoken.set()
        await task
        voice.runtime.publish.assert_awaited_once()
        assert json.loads(json.dumps(voice.runtime.publish.await_args.args[0])) == {
            "type": "realtime_control_requested", "action": "stop", "call_id": "owning-tab"}
        voice.controller.stop.assert_not_awaited()  # finish_call owns orderly closure.
        assert voice.call_id is None
    asyncio.run(run())


def test_unfinished_phrase_keeps_call_and_reports_failure():
    async def run():
        voice = api(active=True)
        voice.controller.finish_call.side_effect = TimeoutError("still speaking")
        await voice.handle("stop", {}, command_id="turn")
        voice.observe(completion())
        await voice.control_task
        assert voice.call_id == "owning-tab"
        voice.runtime.publish.assert_awaited_once()
        assert json.loads(json.dumps(voice.runtime.publish.await_args.args[0])) == {
            "type": "realtime_control_failed", "action": "stop", "error": "still speaking"}
        assert voice.control_request is None
    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "stop"])
def test_failed_command_discards_deferred_control(action):
    async def run():
        voice = api(active=action == "stop")
        await voice.handle(action, {}, command_id="turn")
        voice.observe(completion(failed=True))
        assert voice.control_request is None and voice.control_task is None
        voice.transport.send_primary.assert_not_awaited()
        voice.controller.finish_call.assert_not_awaited()
    asyncio.run(run())


def test_stale_stop_does_not_close_a_replacement_call():
    async def run():
        voice = api(active=True)
        await voice.handle("stop", {}, command_id="turn")
        voice.call_id = "replacement"
        voice.observe(completion())
        await voice.control_task
        voice.controller.finish_call.assert_not_awaited()
        voice.runtime.publish.assert_not_awaited()
        assert voice.call_id == "replacement"
    asyncio.run(run())


def test_guards_and_idempotency():
    async def run():
        voice = api()
        with pytest.raises(ValueError, match="active agent command"):
            await voice.handle("start", {})
        voice.transport.primary_event_connection_count = 0
        with pytest.raises(RuntimeError, match="not connected"):
            await voice.handle("start", {}, command_id="turn")
        assert await voice.handle("stop", {}, command_id="turn") == {"status": "already_stopped"}
        voice.transport.primary_event_connection_count = 1
        await voice.handle("start", {}, command_id="turn")
        with pytest.raises(RuntimeError, match="already pending"):
            await voice.handle("start", {}, command_id="turn")
        voice = api(active=True)
        assert await voice.handle("start", {}, command_id="turn") == {
            "status": "already_active", "call_id": "owning-tab"}
    asyncio.run(run())


def test_shutdown_cancels_pending_drain_without_leaking_a_task():
    async def run():
        voice = api(active=True)
        draining = asyncio.Event()
        async def finish(**_options):
            draining.set()
            await asyncio.Event().wait()
        voice.controller.finish_call.side_effect = finish
        await voice.handle("stop", {}, command_id="turn")
        voice.observe(completion())
        task = voice.control_task
        await draining.wait()
        await voice.shutdown()
        assert task.cancelled()
        assert voice.control_task is None and voice.control_request is None
        voice.controller.stop.assert_awaited_once()
    asyncio.run(run())
