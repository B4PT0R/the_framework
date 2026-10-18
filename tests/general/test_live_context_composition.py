"""Optional context producers compose without product-specific voice subclasses."""
import asyncio
from types import SimpleNamespace

import pytest

from the_framework.plugins.realtime.controller import RealtimeController
from the_framework.plugins.memory.live import MemoryLiveContext
from the_framework.plugins.registry.live import RegistryLiveContext


def controller(factories=()):
    return RealtimeController(SimpleNamespace(register_observer=lambda observer: None),
                              context_factories=factories)


def test_context_lifecycle_accepts_sync_and_async_producers():
    events = []

    class Context:
        recoverable_context_prefixes = ("custom:",)
        def __init__(self, voice):
            self.voice = voice
        def start(self, projection):
            events.append(("start", projection))
        async def ready(self):
            events.append("ready")
        def flush(self):
            events.append("flush")
        async def handle_worker_output(self, output):
            events.append(("worker", output))
        def close(self):
            events.append("close")

    voice = controller((Context,))
    async def scenario():
        voice.start_context({"test": True})
        await voice.ready_context()
        await voice.flush_context()
        await voice.worker_context("output")
        await voice.close_context()
    asyncio.run(scenario())
    assert events == [("start", {"test": True}), "ready", "flush", ("worker", "output"), "close"]
    assert voice.recoverable_context_prefixes == ("custom:",)
    assert voice.contexts[0].voice is voice


def test_voice_without_optional_contexts_has_no_extra_producers():
    voice = controller()
    async def scenario():
        voice.start_context({})
        await voice.flush_context()
        await voice.worker_context(None)
        await voice.close_context()
    asyncio.run(scenario())
    assert voice.contexts == ()


def test_all_producers_are_released_when_one_cleanup_fails():
    released = []
    class First:
        def __init__(self, _voice): pass
        def close(self): released.append("first")
    class Failing:
        def __init__(self, _voice): pass
        async def close(self):
            released.append("failing")
            raise RuntimeError("cleanup failed")
    voice = controller((First, Failing))
    with pytest.raises(RuntimeError, match="cleanup failed"):
        asyncio.run(voice.close_context())
    assert released == ["failing", "first"]


def test_memory_and_registry_reset_between_voice_calls():
    voice = controller((MemoryLiveContext, RegistryLiveContext))
    memory, registry = voice.contexts
    voice.start_context({"providers": ["relevant_memory", "persistent_registry"], "provider_slots": {"relevant_memory": {"text": '<context_provider><retrieved_memories><memory id="one">One</memory></retrieved_memories></context_provider>'}}})
    assert memory.memory_context_ids == {"one"}
    registry.revision = 42
    asyncio.run(voice.close_context())
    assert memory.memory_context_ids == set()
    assert registry.revision == 0
    voice.start_context({"providers": ["relevant_memory", "persistent_registry"], "provider_slots": {"relevant_memory": {"text": '<context_provider><retrieved_memories><memory id="two">Two</memory></retrieved_memories></context_provider>'}}})
    assert memory.memory_context_ids == {"two"}


def test_memory_live_context_owns_candidate_parsing_from_selected_provider_slots():
    from types import SimpleNamespace
    from the_framework.plugins.memory.live import MemoryLiveContext
    from xml.etree import ElementTree
    context = MemoryLiveContext(SimpleNamespace())
    xml = '<context_provider name="relevant_memory"><retrieved_memories><memory id="one">First</memory><memory>No identity</memory><memory id="two">Second</memory></retrieved_memories></context_provider>'
    context.start({'provider_slots': {'relevant_memory': {'text': xml}}})
    assert [entry['id'] for entry in context.memory_candidates] == ['one', 'two']
    assert context.memory_context_ids == {'one', 'two'}
    assert all(len(ElementTree.fromstring(entry['text']).findall('.//memory')) == 1 for entry in context.memory_candidates)
    for slots in ({}, {'other_provider': {'text': xml}}, {'relevant_memory': {'text': '<incomplete'}}, {'relevant_memory': {'text': 'ordinary text'}}):
        context.start({'provider_slots': slots})
        assert not context.memory_candidates
        assert not context.memory_context_ids
