import asyncio
from types import SimpleNamespace

import pytest
import requests

from harness_core.agent import Agent
from harness_core.agent.runtime.agentic_loop import AgenticLoop, GenerationUnavailable
from harness_core.agent.models.responses import FunctionCall, FunctionCallOutput


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
