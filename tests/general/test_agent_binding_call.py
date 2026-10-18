"""Binding changes initiated by a tool cross the same server/worker boundary."""

import asyncio

import pytest

from the_framework import (
    AgentSpec,
    ApplicationSpec,
    BuildContext,
    Plugin,
    ServerExtension,
)
from the_framework.agent.extensions.extension import AgentExtension
from the_framework.agent.models.events import Event
from the_framework.agent.runtime.protocol import PromptRequest, ShutdownRequest
from the_framework.agent.runtime.worker import Worker
from the_framework.plugins.realtime import REALTIME
from the_framework.plugins.system import SystemExtension
from the_framework.server.runtime.application import ApplicationRuntime


@pytest.mark.parametrize("external_first", [False, True])
@pytest.mark.parametrize("with_voice", [False, True])
def test_agent_tool_changes_binding_without_ending_its_turn(tmp_path, external_first, with_voice):
    async def run():
        class Feature(AgentExtension):
            name = "reusable_feature"

        class Security:
            async def authenticate(self, request):
                return None

            async def authorize(self, principal, requirement, request):
                return False

        spec = ApplicationSpec(
            name="Binding test", version="1", security=Security(),
            primary_agent=AgentSpec(name="agent", session_mode='durable'),
            plugins=(Plugin(name="feature", agent_extension=Feature), Plugin(name="system", agent_extension=SystemExtension),
                     *((REALTIME,) if with_voice else ())),
        )
        agent = spec.compile().primary_agent.build_agent(tmp_path / "session.json")
        agent.client = object()
        incoming = asyncio.Queue()

        class Supervisor:
            session_path = tmp_path / "session.json"
            outputs = asyncio.Queue()

            async def send(self, request):
                await incoming.put(request)

            async def start(self):
                self.worker_task = asyncio.create_task(Worker(agent, incoming.get, self.outputs.put).run())
                return await self.outputs.get()

            async def stop(self):
                await incoming.put(ShutdownRequest(id="stop"))
                await self.worker_task

        runtime = ApplicationRuntime(Supervisor())
        app = ApplicationSpec({**spec, "server_extensions": (ServerExtension(name="runtime", service=runtime),)}).build(
            BuildContext()
        )
        results = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def stream(prompt, **options):
            if prompt == "later":
                yield Event.from_dict({"type": "agent.step.start"})
                return
            entered.set()
            await release.wait()
            result = await agent.extension("system").set_plugin_binding("feature", external_first)
            results.append(result)
            assert agent.extension("feature").activated is external_first
            yield Event.from_dict({"type": "agent.step.start"})

        agent.astream = stream
        agent.agentic_loop.can_steer = lambda: False
        async with app.router.lifespan_context(app):
            if with_voice:
                from tests.fixtures.realtime import Sideband

                voice = app.state.application.services["realtime"]
                voice.call_id = "owning-browser"
                voice.controller.call_id = "transport"
                voice.controller.sideband = Sideband()

                async def reconnection():
                    async for output in runtime.stream():
                        if output.type == "realtime_reconnect_requested":
                            return output

                reconnect = asyncio.create_task(reconnection())
            turn = asyncio.create_task(runtime.command(PromptRequest(id="turn", prompt="change feature"), timeout=2))
            await asyncio.wait_for(entered.wait(), 1)
            if external_first:
                await runtime.submit(PromptRequest(id="later", prompt="later", priority=0))
                sent = asyncio.Event()
                original_send = runtime.supervisor.send

                async def send(request):
                    await original_send(request)
                    if request.type == "plugin_binding_request":
                        sent.set()

                runtime.supervisor.send = send
                external = asyncio.create_task(app.state.application.plugin_host.set_binding("feature", False))
                await asyncio.wait_for(sent.wait(), 1)
            release.set()
            await turn
            if external_first:
                await external
            assert results[0]["binding_enabled"] is external_first
            assert app.state.application.plugin_host.status("feature").binding_enabled is external_first
            assert agent.session.command_status("turn") == "completed"
            assert runtime.active_command_id is None
            assert agent.session.plugins["feature"] is external_first
            if with_voice:
                request = await asyncio.wait_for(reconnect, 1)
                assert request.call_id == "owning-browser"
                assert request.error is None
                assert not voice.controller.active
                assert not runtime.active_command_id
                assert voice.call_id is None
        restored = spec.compile().primary_agent.build_agent(tmp_path / "session.json")
        assert restored.extension("feature").activated is external_first

    asyncio.run(run())
