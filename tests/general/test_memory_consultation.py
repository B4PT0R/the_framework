"""Full-memory synthesis is isolated from curation and returned to its caller."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from the_framework.agent import Agent, AgentResult
from the_framework.plugins.memory.agent import MemoryExtension
from the_framework.plugins.memory.server import MemoryConsultations


@pytest.fixture
def memory(tmp_path):
    class ApplicationMemory(MemoryExtension):
        curator_instructions = "Preserve supported evidence."
    return ApplicationMemory(Agent(tmp_path / "session.json")).load()


def test_consultation_has_no_curation_tools_or_retained_history(memory):
    declaration = next(value for kind, value in memory._contributions
                       if kind == "agents" and value.name == "memory.consultation")
    assert declaration.spec.extensions == ()
    assert not declaration.retain_history
    assert not declaration.observes
    assert declaration.completion_tool is None
    assert declaration.spec.configuration["model"] == memory.config.model
    assert "memory_curator" not in declaration.spec.configuration
    assert "contradictions" in declaration.spec.instructions[0].content


def test_ask_reads_all_active_memories_without_embeddings_or_mutations(memory, monkeypatch):
    async def run():
        entries = [memory.store.add(key=key, title=key, content=f"Fact about {key}.",
                                   embedding=[], source_item_ids=[f"source-{key}"])
                   for key in ("science", "courses", "work")]
        monkeypatch.setattr(memory, "ranked", lambda query: pytest.fail("semantic ranking used"))
        call = AsyncMock(return_value={"status": "answered", "answer": "Synthesis"})
        monkeypatch.setattr(memory.agent.bridge, "call", call)
        before = [dict(entry) for entry in memory.store.active()]
        result = await memory.ask("  How do these connect?  ")
        assert result["answer"] == "Synthesis"
        args, kwargs = call.call_args
        assert args[:2] == ("memory", "ask")
        assert args[2]["question"] == "How do these connect?"
        for entry in entries:
            assert entry.id in args[2]["memories"]
            assert entry.content in args[2]["memories"]
        assert args[2]["watermark"] == dict(memory.agent.session.watermark())
        assert kwargs["timeout_ms"] == 180_000
        assert [dict(entry) for entry in memory.store.active()] == before
    asyncio.run(run())


def test_empty_question_and_empty_memory(memory):
    async def run():
        with pytest.raises(ValueError, match="must not be empty"):
            await memory.ask(" ")
        assert (await memory.ask("What is known?"))["status"] == "empty"
    asyncio.run(run())


def result(request_id, *, status="completed", output="Answer with memory IDs."):
    return AgentResult(id="result", task_id="task", profile="memory.consultation",
                       status=status, output=output, error="failure" if status == "failed" else None,
                       watermark={"session_id": "s", "revision": 0, "anchor_id": None, "tail_start": 0},
                       metadata={"memory_consultation_id": request_id})


@pytest.fixture
def service():
    runtime = SimpleNamespace(fleet=SimpleNamespace(submit=AsyncMock()), fleet_result_handlers=set())
    runtime.register_fleet_result_handler = runtime.fleet_result_handlers.add
    runtime.unregister_fleet_result_handler = runtime.fleet_result_handlers.discard
    return MemoryConsultations(runtime)


def payload():
    return {"question": "A < B?", "memories": "<memory_table />",
            "watermark": {"session_id": "s", "revision": 0, "anchor_id": None, "tail_start": 0}}


def test_service_correlates_inline_answer_and_cleans_up(service):
    async def run():
        async def submit(profile, **kwargs):
            assert profile == "memory.consultation"
            assert "A &lt; B?" in kwargs["prompt"]
            service.receive(result(kwargs["metadata"]["memory_consultation_id"]))
            return True
        service.runtime.fleet.submit.side_effect = submit
        answer = await service.handle("ask", payload())
        assert answer["status"] == "answered"
        assert answer["answer"] == "Answer with memory IDs."
        assert not service.pending
        service.receive(result(answer["request_id"]))  # late/duplicate results are harmless
    asyncio.run(run())


def test_unavailable_and_failed_consultations_are_not_answers(service):
    async def run():
        service.runtime.fleet.submit.return_value = False
        with pytest.raises(RuntimeError, match="unavailable"):
            await service.handle("ask", payload())
        assert not service.pending
        async def submit(profile, **kwargs):
            service.receive(result(kwargs["metadata"]["memory_consultation_id"], status="failed"))
            return True
        service.runtime.fleet.submit.side_effect = submit
        with pytest.raises(RuntimeError, match="failure"):
            await service.handle("ask", payload())
        assert not service.pending
    asyncio.run(run())


def test_cancellation_and_service_shutdown_release_waiters(service):
    async def run():
        task = asyncio.create_task(service.handle("ask", payload()))
        await asyncio.sleep(0)
        assert service.pending
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.pending
        service.runtime.register_fleet_result_handler(service.receive)
        task = asyncio.create_task(service.handle("ask", payload()))
        await asyncio.sleep(0)
        await service.stop()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.pending
        assert not service.runtime.fleet_result_handlers
    asyncio.run(run())




def test_consumed_results_do_not_queue_behind_the_calling_turn():
    from the_framework.server.runtime.session import SessionRuntime

    async def run():
        runtime = object.__new__(SessionRuntime)
        runtime.fleet = SimpleNamespace(results=asyncio.Queue(), acknowledge=AsyncMock())
        consumed = result("inline")
        ordinary = result("ordinary")
        ordinary.profile = "memory.jiminy"
        runtime.fleet_result_handlers = [lambda value: value.profile == "memory.consultation"]
        runtime.command = AsyncMock()
        runtime.fleet_deliveries = asyncio.Queue()
        await runtime.fleet.results.put(consumed)
        await runtime.fleet.results.put(ordinary)
        delivery = asyncio.create_task(runtime.deliver_fleet_results())
        relay = asyncio.create_task(runtime.relay_fleet_results())
        await asyncio.wait_for(runtime.fleet.results.join(), timeout=1)
        await asyncio.wait_for(runtime.fleet_deliveries.join(), timeout=1)
        delivery.cancel()
        with pytest.raises(asyncio.CancelledError):
            await delivery
        relay.cancel()
        with pytest.raises(asyncio.CancelledError):
            await relay
        assert runtime.fleet.acknowledge.await_count == 2
        assert runtime.command.await_count == 1
        assert runtime.command.call_args.args[0].payload.profile == "memory.jiminy"
    asyncio.run(run())


def test_parallel_consultations_keep_answers_separate(service):
    async def run():
        first = asyncio.create_task(service.handle("ask", {**payload(), "question": "first"}))
        second = asyncio.create_task(service.handle("ask", {**payload(), "question": "second"}))
        await asyncio.sleep(0)
        calls = service.runtime.fleet.submit.call_args_list
        assert len(calls) == 2
        for call in reversed(calls):
            request_id = call.kwargs["metadata"]["memory_consultation_id"]
            response = result(request_id, output=call.kwargs["prompt"])
            assert service.receive(response) is True
        answers = await asyncio.gather(first, second)
        assert "first" in answers[0]["answer"]
        assert "second" in answers[1]["answer"]
        assert not service.pending
    asyncio.run(run())


def test_timeout_is_not_an_answer_and_releases_waiter(service, monkeypatch):
    original_wait = asyncio.wait_for
    monkeypatch.setattr("the_framework.plugins.memory.server.asyncio.wait_for",
                        lambda future, timeout: original_wait(future, timeout=0.001))
    async def run():
        with pytest.raises(TimeoutError):
            await service.handle("ask", payload())
        assert not service.pending
    asyncio.run(run())


def test_inline_answer_bypasses_blocked_canonical_delivery():
    from the_framework.server.runtime.session import SessionRuntime

    async def run():
        runtime = object.__new__(SessionRuntime)
        runtime.fleet = SimpleNamespace(results=asyncio.Queue(), acknowledge=AsyncMock())
        runtime.fleet_deliveries = asyncio.Queue()
        inline_received = asyncio.Event()
        canonical_started = asyncio.Event()
        release = asyncio.Event()
        def receive(value):
            if value.profile == "memory.consultation":
                inline_received.set()
                return True
        runtime.fleet_result_handlers = {receive}
        async def command(value):
            canonical_started.set()
            await release.wait()
        runtime.command = command
        delivery = asyncio.create_task(runtime.deliver_fleet_results())
        relay = asyncio.create_task(runtime.relay_fleet_results())
        ordinary = result("ordinary")
        ordinary.profile = "memory.jiminy"
        await runtime.fleet.results.put(ordinary)
        await asyncio.wait_for(canonical_started.wait(), timeout=1)
        inline = result("inline")
        await runtime.fleet.results.put(inline)
        await asyncio.wait_for(inline_received.wait(), timeout=1)
        await asyncio.wait_for(runtime.fleet.results.join(), timeout=1)
        assert runtime.fleet.acknowledge.call_args.args[0] is inline
        release.set()
        await asyncio.wait_for(runtime.fleet_deliveries.join(), timeout=1)
        for task in (delivery, relay):
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(run())
