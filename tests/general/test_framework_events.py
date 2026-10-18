"""Public event boundaries: normalized signals, durable commits and plugin ownership."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from the_framework.agent import Agent, Event, LocalEvent, Message, AgentPluginBindingUpdated
from the_framework.agent.models.lifecycle import AgentTextDelta
from the_framework.agent.runtime.protocol import CommandEvent, SubagentEvent
from the_framework.agent.runtime.responses_events import ResponsesEvents
from the_framework.plugins.realtime.controller import RealtimeController, RealtimeEvent
from the_framework.plugins.realtime.live_events import LiveEvents


def test_backend_stream_is_converted_before_hooks_and_only_commits_final_items(tmp_path):
    item = Message(id="reply", role="assistant", content=[{"type": "output_text", "text": "Hello"}])
    source = [
        {"type": "response.created", "response": {"private": "not-public"}},
        {"type": "response.output_item.added", "item": {**json.loads(json.dumps(item)), "content": []}},
        {"type": "response.output_text.delta", "item_id": "reply", "delta": "Hello", "sequence_number": 999,
         "logprobs": ["private"], "private_field": True},
        {"type": "response.future_diagnostic", "private": "not-public"},
        {"type": "response.output_item.done", "item": item},
        {"type": "response.completed", "response": {"usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}}},
    ]
    agent = Agent(tmp_path / "session.json", client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_: iter(source))))
    observed, hooks = [], []
    agent.on("*", observed.append)
    def on_event(event):
        hooks.append(event)
        assert not event.type.startswith("response.")
        if isinstance(event, AgentTextDelta):
            assert not agent.session.history
        return event
    agent.hooks.add(on_event)
    agent.agentic_loop.generation({"stream": True})
    assert [e.type for e in observed] == ["agent.generation.start", "agent.output_item.started", "agent.text.delta",
                                            "agent.response_item.added", "agent.generation.end"]
    delta = observed[2]
    assert dict(delta) == {"type": "agent.text.delta", "item_id": "reply", "channel": "output", "part_index": 0, "delta": "Hello", "sequence": 0}
    assert agent.session.history == [item]
    assert observed[-1].status == "completed"
    assert observed[-1].usage.total_tokens == 3
    assert [e.type for e in hooks] == ["agent.output_item.started", "agent.text.delta", "agent.response_item.added"]
    assert all(Event.from_dict(json.loads(json.dumps(e))).type == e.type for e in observed)


@pytest.mark.parametrize("channel,wire", [("output", "output_text"), ("refusal", "refusal"), ("reasoning", "reasoning_text"), ("summary", "reasoning_summary_text")])
def test_text_channels_have_framework_sequence_and_part_identity(channel, wire):
    adapter = ResponsesEvents()
    first, = adapter.convert({"type": f"response.{wire}.delta", "item_id": "item", "delta": "text", "sequence_number": 888,
                              "content_index": 2, "summary_index": 3})
    final, = adapter.convert({"type": f"response.{wire}.done", "item_id": "item", "text": "text", "refusal": "text", "sequence_number": -1})
    assert (first.channel, first.part_index, first.sequence) == (channel, 3 if channel == "summary" else 2, 0)
    assert (final.channel, final.text, final.sequence) == (channel, "text", 1)


@pytest.mark.parametrize("wire", ["function_call_arguments", "custom_tool_call_input", "mcp_call_arguments", "code_interpreter_call_code"])
def test_tool_inputs_are_normalized_and_binary_chunks_never_become_presentations(wire):
    adapter = ResponsesEvents()
    start, = adapter.convert({"type": f"response.{wire}.delta", "item_id": "call", "delta": "input"})
    done, = adapter.convert({"type": f"response.{wire}.done", "item_id": "call", "arguments": "input"})
    assert start.type == "agent.tool_input.delta"
    assert done.type == "agent.tool_input.done"
    assert done.text == "input"
    for kind in ["response.image_generation_call.partial_image", "response.audio.delta", "response.output_text.annotation.added", "response.future.unknown"]:
        assert adapter.convert({"type": kind, "data": "not-public"}) == ()
    status, = adapter.convert({"type": "response.web_search_call.searching", "item_id": "search"})
    assert (status.type, status.tool, status.status) == ("agent.remote_tool.status", "web_search", "running")


def test_undeclared_backend_events_cannot_cross_command_subagent_or_voice_envelopes():
    for model, fields in [(CommandEvent, {"command_id": "main"}), (SubagentEvent, {"session_id": "child", "agent": "agent", "task_id": "task"}), (RealtimeEvent, {"call_id": "call"})]:
        with pytest.raises(ValueError, match="unknown Framework event"):
            model(**fields, event={"type": "response.output_text.delta", "delta": "backend"})
    assert isinstance(Event.from_dict(dict(AgentPluginBindingUpdated(plugin="x", enabled=True))), AgentPluginBindingUpdated)
    with pytest.raises(KeyError):
        AgentTextDelta(item_id="x", delta="y", sequence=0, backend_field=True)


def test_plugin_declared_event_needs_no_worker_core_branch():
    class LightsChanged(LocalEvent):
        type: str = "lights.changed"
        brightness: int
    event = LightsChanged(brightness=42)
    assert isinstance(Event.from_dict(dict(event)), LightsChanged)
    assert isinstance(CommandEvent(command_id="turn", event=dict(event)).event, LightsChanged)
    agent = Agent(client=object())
    received = []
    agent.on("lights.changed", received.append)
    agent.emit(event)
    assert received == [event]


def test_live_normalization_owns_incremental_cumulative_and_duplicate_transcripts():
    adapter = LiveEvents()
    def caption(text):
        return adapter.convert({"type": "input_transcript.added", "item": {"text": text}})
    assert caption("Hello")[0].text == "Hello"
    assert caption("Hello") == ()
    assert caption("Hello there")[0].text == "Hello there"
    assert caption(" there") == ()
    assert caption("!")[0].text == "Hello there!"
    final, = adapter.convert({"type": "turn.done", "turn": {"id": "turn", "role": "user", "transcript": "Hello there!"}})
    assert (final.type, final.turn_id, final.transcript) == ("voice.turn.completed", "turn", "Hello there!")
    assert caption("Next")[0].text == "Next"
    assert adapter.convert({"type": "session.updated", "private": True}) == ()


def test_live_reader_never_publishes_backend_wire_events():
    async def run():
        published = []
        controller = RealtimeController(SimpleNamespace(register_observer=lambda _: None, publish=lambda e: capture(e)))
        async def capture(event):
            published.append(event)
        async def handle(_):
            return False
        controller._handle_sideband_event = handle
        events = iter([{"type": "session.created", "private": True},
                       {"type": "input_transcript.added", "item": {"text": "Hi"}},
                       {"type": "response.created", "response": {"private": True}},
                       {"type": "error", "error": {"message": "Closed", "code": "closed", "private": True}}])
        controller.sideband = SimpleNamespace(recv=lambda: next(events))
        controller.call_id = "voice"
        await controller._read_sideband()
        assert [e.event.type for e in published] == ["voice.transcript.updated", "voice.response.state", "voice.error"]
        assert all("private" not in json.dumps(e) for e in published)
        assert all(isinstance(RealtimeEvent(**json.loads(json.dumps(e))).event, Event) for e in published)
    asyncio.run(run())


@pytest.mark.parametrize("wire,status", [("response.incomplete", "incomplete"), ("response.failed", "failed"), ("error", "failed")])
def test_generation_terminal_outcome_is_explicit_even_without_backend_details(wire, status):
    source = {"type": wire, "response": {}}
    agent = Agent(client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_: iter([source]))))
    events = []
    agent.on("*", events.append)
    if status == "failed":
        with pytest.raises(RuntimeError):
            agent.agentic_loop.generation({"stream": True})
    else:
        agent.agentic_loop.generation({"stream": True})
    assert events[-1].type == "agent.generation.end"
    assert events[-1].status == status
    assert events[-1].usage is None
    assert bool(events[-1].error) is (status == "failed")


def test_voice_delegation_consumes_framework_stream_and_ignores_reasoning():
    from the_framework.agent.models.lifecycle import AgentOutputItemStarted, AgentTextDone, AgentToolCallStart
    from the_framework.agent.runtime.protocol import CommandCompleted, PromptRequest
    async def run():
        delivered, published = [], []
        async def publish(event):
            published.append(event)
        voice = RealtimeController(SimpleNamespace(register_observer=lambda _: None, publish=publish))
        voice.call_id = "voice"
        voice.delegations["cmd"] = {"id": "delegation", "command_id": "cmd", "items": {}, "fallback": "", "final": ""}
        async def send(delegation_id, text, *, channel="speakable"):
            delivered.append((channel, text))
        voice._send_delegation = send
        events = [
            AgentOutputItemStarted(item=Message(id="progress", role="assistant", phase="commentary", content=[])),
            AgentTextDelta(item_id="progress", delta="Checking", channel="output", sequence=0),
            AgentTextDone(item_id="progress", text="Checking", channel="output", sequence=1),
            AgentToolCallStart(tool_call={"name": "tool"}, user_feedback="Using the tool"),
            AgentOutputItemStarted(item=Message(id="answer", role="assistant", phase="final_answer", content=[])),
            AgentTextDelta(item_id="answer", delta="private reasoning", channel="reasoning", sequence=2),
            AgentTextDelta(item_id="answer", delta="Answer", channel="output", sequence=3),
        ]
        for event in events:
            await voice.handle_worker_output(CommandEvent(command_id="cmd", event=event))
        await voice.handle_worker_output(CommandCompleted(command=PromptRequest(id="cmd", prompt="request")))
        assert delivered == [("commentary", "Checking"), ("commentary", "Using the tool"), ("speakable", "Answer")]
        assert published[-1].state == "completed"
    asyncio.run(run())


def test_transport_rejection_fails_the_waiting_command_with_a_clear_error(tmp_path):
    from the_framework.server.runtime.session import SessionRuntime
    from the_framework.server.runtime.supervisor import WorkerTransportError
    from the_framework.agent.runtime.protocol import PromptRequest
    async def run():
        async def send(_):
            await runtime.publish(WorkerTransportError(error="undeclared backend event"))
        runtime = SessionRuntime(SimpleNamespace(send=send, session_path=tmp_path / "session.json"))
        with pytest.raises(RuntimeError, match="worker transport failed: undeclared backend event"):
            await runtime.command(PromptRequest(id="command", prompt="request"))
    asyncio.run(run())
