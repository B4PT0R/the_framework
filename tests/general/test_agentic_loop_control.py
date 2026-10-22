import asyncio
from types import SimpleNamespace

import pytest
import requests

from the_framework.agent import Agent
from the_framework.agent.runtime.agentic_loop import AgenticLoop, GenerationUnavailable
from the_framework.agent.models.responses import FunctionCall, FunctionCallOutput


def test_response_kind_matches_stream_and_canonical_replay_and_is_scoped(tmp_path):
    from the_framework.agent import Session
    from the_framework.agent.context.builder import Context

    class Responses:
        counter = 0

        def create(self, **_payload):
            self.counter += 1
            identity = f"answer-{self.counter}"
            return iter([
                {"type": "response.output_text.delta", "item_id": identity,
                 "delta": "Answer", "sequence_number": 1, "content_index": 0},
                {"type": "response.output_item.done", "sequence_number": 2,
                 "item": {"type": "message", "id": identity, "role": "assistant",
                          "content": [{"type": "output_text", "text": "Answer"}]}},
            ])

    agent = Agent(client=SimpleNamespace(responses=Responses()), session_path=tmp_path / "session.json")
    loop = agent.agentic_loop
    events = []
    agent.events.subscribe("*", events.append)

    async def step():
        loop.generation({"stream": True})

    loop.step = step
    asyncio.run(loop.turn("Voice question", response_kind="delegation"))
    assert loop.response_kind == "message"
    asyncio.run(loop.turn("Text question"))
    assert [event.kind for event in events if event.type == "agent.text.delta"] == ["delegation", "message"]
    restored = Session.open(agent.session.path)
    assert [item.kind for item in restored.history if item.get("role") == "assistant"] == ["delegation", "message"]
    assert all("kind" not in item for item in Context.project(restored.history))

    async def fail():
        raise RuntimeError("interrupted generation")

    loop.step = fail
    with pytest.raises(RuntimeError):
        asyncio.run(loop.turn("Another voice question", response_kind="delegation"))
    assert loop.response_kind == "message"


def test_interrupt_finishes_current_tool_and_marks_remaining_calls_skipped():
    async def test():
        agent = Agent(client=object())
        loop = AgenticLoop(agent)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []
        first = FunctionCall(name="first", call_id="call-1", arguments="{}")
        second = FunctionCall(name="second", call_id="call-2", arguments="{}")

        async def run_tool_call(tool_call):
            calls.append(tool_call.name)
            started.set()
            await release.wait()
            return tool_call.name

        loop.run_tool_call = run_tool_call
        running = asyncio.create_task(loop.run_tool_calls([first, second]))
        await started.wait()
        loop.interrupt("stop requested")
        release.set()
        await running

        assert calls == ["first"]
        skipped = [
            item for item in agent.session.history
            if isinstance(item, FunctionCallOutput) and item.call_id == "call-2"
        ]
        assert len(skipped) == 1
        assert "before it started" in skipped[0].output

    asyncio.run(test())


def test_generation_retries_transient_failure_before_first_event(monkeypatch):
    attempts = []

    class Responses:
        def create(self, **_payload):
            attempts.append(True)
            if len(attempts) == 1:
                raise AttributeError("'NoneType' object has no attribute 'read'")
            return []

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    loop.generation({"stream": True})

    assert len(attempts) == 2


def test_generation_does_not_retry_after_first_event(monkeypatch):
    attempts = []

    class BrokenStream:
        def __iter__(self):
            yield {"type": "response.created", "response": {}, "sequence_number": 1}
            raise requests.ConnectionError("stream disconnected")

    class Responses:
        def create(self, **_payload):
            attempts.append(True)
            return BrokenStream()

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    with pytest.raises(requests.ConnectionError, match="stream disconnected"):
        loop.generation({"stream": True})

    assert len(attempts) == 1


def test_generation_retries_streamed_overload_before_output(monkeypatch):
    attempts = []

    class Responses:
        def create(self, **_payload):
            attempts.append(True)
            if len(attempts) == 1:
                return iter([
                    {"type": "response.created", "response": {}, "sequence_number": 1},
                    {"type": "response.in_progress", "response": {}, "sequence_number": 2},
                    {
                        "type": "error",
                        "error": {
                            "type": "service_unavailable_error",
                            "code": "server_is_overloaded",
                            "message": "Please try again later.",
                        },
                        "sequence_number": 3,
                    },
                ])
            return []

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    loop.generation({"stream": True})

    assert len(attempts) == 2


def test_generation_accepts_typed_stream_events(monkeypatch):
    attempts = []

    class Responses:
        def create(self, **_payload):
            attempts.append(True)
            if len(attempts) == 1:
                return iter([
                    SimpleNamespace(
                        type="response.created", response={}, sequence_number=1,
                        model_dump=lambda: {
                            "type": "response.created",
                            "response": {},
                            "sequence_number": 1,
                        },
                    ),
                    SimpleNamespace(
                        type="error",
                        error=SimpleNamespace(
                            type="service_unavailable_error",
                            code="server_is_overloaded",
                            message="Please try again later.",
                        ),
                        sequence_number=2,
                    ),
                ])
            return []

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    loop.generation({"stream": True})

    assert len(attempts) == 2


def test_generation_fails_on_non_retryable_stream_failure(monkeypatch):
    class Responses:
        def create(self, **_payload):
            return iter([{
                "type": "response.failed",
                "response": {
                    "error": {"code": "invalid_request", "message": "Bad input"},
                },
                "sequence_number": 1,
            }])

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    with pytest.raises(RuntimeError, match="invalid_request: Bad input"):
        loop.generation({"stream": True})


def test_generation_reports_bounded_unavailability(monkeypatch):
    attempts = []

    class Responses:
        def create(self, **_payload):
            attempts.append(True)
            return None

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = AgenticLoop(agent)
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)

    with pytest.raises(GenerationUnavailable, match="after 4 attempts"):
        loop.generation({"stream": True})

    assert len(attempts) == 4


def test_steering_checkpoint_preserves_fifo_and_requests_another_step():
    agent = Agent(client=object())
    loop = AgenticLoop(agent)
    first = loop.queue_steering("first")
    second = loop.queue_steering("second")

    applied = loop.checkpoint()

    assert applied == (first, second)
    assert [item.content[0].text for item in agent.session.history] == [
        "first",
        "second",
    ]
    assert loop.request_next_step is True


def test_sdk_routing_scope_spans_retries_and_steps_but_not_turns(tmp_path, monkeypatch):
    import json
    from codex_backend_sdk import CodexClient

    class SSE:
        headers = {}

        def __init__(self, events):
            self.events = events

        def iter_lines(self):
            for event in self.events:
                yield "data: " + json.dumps(event)
                yield ""

        def close(self):
            pass

    metadata = {"type": "response.metadata", "headers": {"x-codex-turn-state": "opaque-route"}}
    overloaded = {"type": "error", "error": {"type": "service_unavailable_error", "message": "retry"}}
    completed = {"type": "response.completed", "response": {"id": "resp-test", "model": "test"}}
    replies = iter([SSE([metadata, overloaded]), SSE([completed]), SSE([completed]), SSE([completed])])
    posts = []
    client = CodexClient(model="test")

    def post(_path, **kwargs):
        posts.append(kwargs)
        return next(replies)

    monkeypatch.setattr(client, "_post", post)
    agent = Agent(client=client, session_path=tmp_path / "session.json")
    loop = agent.agentic_loop
    monkeypatch.setattr(loop.retry_wake, "wait", lambda _delay: False)
    steps = []

    async def step():
        steps.append(True)
        loop.generation({"input": "hello", "stream": True, "prompt_cache_key": "conversation"})
        loop.request_next_step = len(steps) == 1

    monkeypatch.setattr(loop, "step", step)
    asyncio.run(loop.turn("First turn"))
    assert loop.responses is None
    asyncio.run(loop.turn("Next turn"))
    assert [post["headers"].get("x-codex-turn-state") for post in posts] == [
        None, "opaque-route", "opaque-route", None,
    ]
    assert all(post["body"] == posts[0]["body"] for post in posts)
    assert "opaque-route" not in agent.session.path.read_text()
    assert loop.responses is None


def test_routing_scope_closes_on_turn_failure(monkeypatch):
    from contextlib import contextmanager

    closed = []
    scoped = SimpleNamespace(create=lambda **kwargs: [])

    class Responses:
        @contextmanager
        def turn(self):
            try:
                yield scoped
            finally:
                closed.append(True)

    agent = Agent(client=SimpleNamespace(responses=Responses()))
    loop = agent.agentic_loop

    async def fail():
        assert loop.responses is scoped
        raise RuntimeError("interrupted")

    monkeypatch.setattr(loop, "step", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(loop.turn("question"))
    assert closed == [True]
    assert loop.responses is None
