"""Binding changes initiated by a tool cross the same server/worker boundary."""

import asyncio

import pytest

from the_framework import AgentApplication, AgentSpec, BuildContext, Extension, Plugin, SessionPolicy
from the_framework.agent.extensions.plugin import AgentPlugin
from the_framework.agent.models.events import Event
from the_framework.agent.runtime.protocol import PromptRequest, ShutdownRequest
from the_framework.agent.runtime.worker import Worker
from the_framework.plugins.system import SystemPlugin
from the_framework.server.runtime.application import ApplicationRuntime


@pytest.mark.parametrize("external_first", [False, True])
def test_agent_tool_changes_binding_without_ending_its_turn(tmp_path, external_first):
    async def run():
        class Feature(AgentPlugin):
            name = "feature"

        spec = AgentApplication(
            name="Binding test", version="1",
            primary_agent=AgentSpec(name="agent", session=SessionPolicy.durable()),
            plugins=(Plugin(name="feature", agent=Feature), Plugin(name="system", agent=SystemPlugin)),
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
        app = AgentApplication({**spec, "extensions": (Extension(name="runtime", service=runtime),)}).build(
            BuildContext({"plugin_state_path": tmp_path / "plugins.json"})
        )
        results = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def stream(prompt, **options):
            if prompt == "later":
                yield Event.from_dict({"type": "unknown"})
                return
            entered.set()
            await release.wait()
            result = await agent.plugin("system").set_plugin_binding("feature", external_first)
            results.append(result)
            assert agent.plugin("feature").activated is external_first
            yield Event.from_dict({"type": "unknown"})

        agent.astream = stream
        agent.agentic_loop.can_steer = lambda: False
        async with app.router.lifespan_context(app):
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
        restored = spec.compile().primary_agent.build_agent(tmp_path / "session.json")
        assert restored.plugin("feature").activated is external_first

    asyncio.run(run())
