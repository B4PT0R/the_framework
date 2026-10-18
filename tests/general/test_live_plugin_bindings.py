"""Plugin exposure changes preserve history and determine the next voice context."""

import pytest

from the_framework.agent import Agent, Message
from the_framework.agent.extensions.providers import provider
from the_framework.plugins.memory import MemoryExtension
from the_framework.plugins.realtime import RealtimeExtension
from the_framework.plugins.registry import RegistryExtension


@pytest.mark.parametrize('plugin', ['memory', 'registry'])
def test_voice_snapshot_tracks_disabled_and_reenabled_providers_without_writing_history(tmp_path, plugin):
    class ApplicationMemory(MemoryExtension):
        curator_instructions = 'Preserve supported facts.'

        @provider
        def relevant_memory(self):
            return '<retrieved_memories><memory id="one">Memory marker</memory></retrieved_memories>'

    agent = Agent(client=object())
    agent.add_extension(ApplicationMemory)
    registry = agent.add_extension(RegistryExtension(agent, runtime_root=tmp_path))
    registry.set_value('$.marker', '"Registry marker"')
    voice = agent.add_extension(RealtimeExtension)
    voice.config.provider_whitelist = ['relevant_memory', 'persistent_registry']
    message = Message(role='user', content=[{'type': 'input_text', 'text': 'Keep this conversation'}])
    agent.add_response_item(message)

    def projection():
        return voice.session_snapshot({'items': list(agent.session.history), 'extensions': {}})['extensions']['realtime']

    provider_name = 'relevant_memory' if plugin == 'memory' else 'persistent_registry'
    marker = 'Memory marker' if plugin == 'memory' else 'Registry marker'
    initial = projection()
    assert provider_name in initial['provider_slots']
    assert marker in initial['session']['instructions']
    agent.deactivate_extension(plugin)
    disabled = projection()
    assert provider_name not in disabled['provider_slots']
    assert marker not in disabled['session']['instructions']
    if plugin == 'memory':
        assert disabled['memory_candidates'] == []
    agent.activate_extension(plugin)
    restored = projection()
    assert marker in restored['session']['instructions']
    assert agent.session.history == [message]


def test_unselected_live_producers_cannot_deliver_late_delegation_outputs():
    import asyncio
    from types import SimpleNamespace

    from tests.fixtures.realtime import Sideband
    from the_framework.agent.runtime.protocol import CommandEvent
    from the_framework.plugins.memory.live import MemoryLiveContext
    from the_framework.plugins.realtime.controller import RealtimeController
    from the_framework.plugins.registry.live import RegistryLiveContext

    async def run():
        voice = RealtimeController(SimpleNamespace(register_observer=lambda observer: None),
                                   context_factories=(MemoryLiveContext, RegistryLiveContext))
        voice.call_id, voice.sideband, voice.peer_ready = 'call', Sideband(), True
        voice.start_context({'providers': [], 'memory_candidates': []})
        output = CommandEvent(command_id='delegation', event={
            'type': 'agent.response_item.added', 'item': {'registry_delta': {
                'revision': 1, 'operations': [{'op': 'set', 'path': '$.secret', 'value': 'private'}],
            }},
        })
        await voice.worker_context(output)
        await voice.flush_context()
        assert voice.sideband.sent == []
        assert not voice.contexts[0].memory_append_tasks
        voice.start_context({'providers': ['persistent_registry']})
        await voice.worker_context(output)
        assert len(voice.sideband.sent) == 1
        assert 'private' in voice.sideband.sent[0]['content'][0]['text']
        await voice._close()

    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['ready', 'worker_failed', 'shutdown', 'binding_failed', 'voice_disabled'])
def test_binding_reconnect_waits_for_reconciliation_and_cleans_up(outcome):
    import asyncio
    from types import SimpleNamespace

    from tests.fixtures.realtime import Sideband
    from the_framework.plugins.realtime.api import RealtimeApi
    from the_framework.plugins.realtime.controller import RealtimeController

    async def run():
        reconciled = asyncio.get_running_loop().create_future()
        entered = asyncio.Event()
        published, bindings = [], []

        async def apply_event(name, payload, *, timeout):
            assert name == 'realtime_barrier' and payload == {'call_id': 'transport'}
            assert timeout is None
            entered.set()
            await reconciled

        async def publish(output):
            published.append(output)

        async def set_binding(name, enabled, *, call):
            bindings.append((name, enabled))
            if outcome == 'binding_failed':
                raise ValueError('Binding rejected')

        runtime = SimpleNamespace(register_observer=lambda observer: None, apply_event=apply_event,
                                  publish=publish, set_plugin_binding=set_binding)
        voice = RealtimeController(runtime)
        voice.sideband, voice.call_id, voice.peer_ready = Sideband(), 'transport', True
        voice.realtime_response_active = True
        await voice.append_context('Do not replay old context')
        api = RealtimeApi(runtime, voice)
        api.call_id = 'owning-browser'
        plugin = 'realtime' if outcome == 'voice_disabled' else 'registry'
        if outcome == 'binding_failed':
            with pytest.raises(ValueError, match='Binding rejected'):
                await api.update_plugin_binding(plugin, False)
        else:
            await api.update_plugin_binding(plugin, False)
        assert bindings == [(plugin, False)]
        assert not voice.active and api.call_id is None
        assert voice.pending_provider_contexts == []
        assert published == []
        if outcome == 'voice_disabled':
            assert not api._reconnect_tasks
            return
        await entered.wait()
        tasks = tuple(api._reconnect_tasks)
        if outcome == 'shutdown':
            await api.stop()
            assert not api._reconnect_tasks
            assert reconciled.cancelled()
            assert published == []
            return
        if outcome == 'worker_failed':
            reconciled.set_exception(RuntimeError('Worker stopped'))
        else:
            reconciled.set_result(None)
        await asyncio.gather(*tasks)
        assert len(published) == 1
        assert published[0].call_id == 'owning-browser'
        assert bool(published[0].error) == (outcome == 'worker_failed')

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize('role', ['user', 'assistant'])
def test_accepted_voice_turn_is_committed_after_voice_binding_is_disabled(role):
    import asyncio

    from the_framework.agent.runtime.protocol import ExternalEventRequest
    from the_framework.agent.runtime.worker import Worker

    async def run():
        agent = Agent(client=object())
        agent.add_extension(RealtimeExtension)
        # Model a final transcript accepted before call shutdown, whose queued
        # execution follows the higher-priority binding update.
        queued = ExternalEventRequest(id='last-voice-turn', name='realtime_turn', payload={
            'role': role, 'transcript': 'Keep the final words', 'call_id': 'old-call', 'turn_id': 'last',
        })
        agent.deactivate_extension('realtime')
        worker = Worker(agent, None, None)
        outputs = [output async for output in worker.execute(queued)]
        assert any(output.type == 'agent.response_item.added' for output in outputs)
        assert agent.session.history[-1].role == role
        assert agent.session.history[-1].kind == 'realtime'
        assert agent.session.history[-1].content[0].text == 'Keep the final words'
        assert not agent.extension('realtime').activated

    asyncio.run(run())
