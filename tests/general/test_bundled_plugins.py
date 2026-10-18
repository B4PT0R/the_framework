"""Complete feature declarations stay usable in workers and installed apps."""

import subprocess
import sys
from types import SimpleNamespace

from the_framework.server.composition.application import _assemble_server
from the_framework import Application, BuildContext, ServerExtension
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM


class Security:
    async def authenticate(self, request):
        return None

    async def authorize(self, principal, requirement, request):
        return False


def test_complete_plugin_imports_and_worker_compile_do_not_import_server_modules():
    result = subprocess.run([sys.executable, "-c", '''
import sys
from the_framework import Application, AgentSpec, Plugin
from the_framework.plugins.bash import BASH
from the_framework.plugins.browser import CHROMIUM
from the_framework.plugins.memory import MEMORY
from the_framework.plugins.realtime import REALTIME
from the_framework.plugins.registry import REGISTRY
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM
from the_framework.plugins.web_search import WEB_SEARCH
plugins = (BASH, CHROMIUM, MEMORY, REALTIME, REGISTRY, SCHEDULER, SYSTEM, WEB_SEARCH)
assert all(isinstance(plugin, Plugin) for plugin in plugins)
plan = Application(name="Example", version="1", plugins=plugins,
    agents=(AgentSpec(name="primary"),)).compile()
assert "memory.jiminy" in plan.agents
assert len(plan.agents["primary"].extensions) == len(plugins)
for package in ("scheduler", "system", "realtime"):
    assert "the_framework.plugins." + package + ".server" not in sys.modules
assert "the_framework.plugins.realtime.controller" not in sys.modules
assert "the_framework.plugins.realtime.api" not in sys.modules
'''], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_complete_stateful_plugins_install_handlers_and_reset_hooks(tmp_path):
    handlers, observers, hooks = {}, [], []
    runtime = SimpleNamespace(
        bridge=SimpleNamespace(register=lambda name, handler, **kwargs: handlers.setdefault(name, handler)),
        register_observer=observers.append,
        register_reset_hooks=lambda **kwargs: hooks.append(kwargs),
        submit=lambda command: None,
        supervisor=SimpleNamespace(send=lambda command: None),
    )
    context = BuildContext(
        plugins={
            "scheduler": {"state_path": tmp_path / "custom-clock.json"},
            "system": {"state_path": tmp_path / "custom-control.json"},
        },
    )
    app = Application(name="Services", version="1", plugins=(SCHEDULER, SYSTEM),
        server_extensions=(ServerExtension(name="runtime", service=runtime),)).build(context=context)
    scheduler = app.state.application.extensions["scheduler"].service
    system = app.state.application.extensions["system"].service
    assert scheduler.path == tmp_path / "custom-clock.json"
    assert system.path == tmp_path / "custom-control.json"
    assert set(handlers) == {"scheduler", "system"}
    assert len(observers) == len(hooks) == 2
    assert hooks[0] == {"suspend": scheduler.stop, "resume": scheduler.start}
    assert hooks[1]["resume"] == system.start
    assert scheduler.health()["wakes"] == 0


def test_realtime_reset_waits_for_worker_barrier_and_clears_call_identity(tmp_path):
    import asyncio
    from the_framework.agent.runtime.protocol import CommandCompleted
    from the_framework.plugins.realtime import REALTIME
    from the_framework.server import SessionRuntime

    async def run():
        events = []
        barrier_sent = asyncio.Event()
        acknowledge = asyncio.Event()
        supervisor = SimpleNamespace(outputs=asyncio.Queue())

        async def send(command):
            events.append("voice barrier")
            barrier_sent.set()
            await acknowledge.wait()
            await supervisor.outputs.put(CommandCompleted(request_id=command.id, command=command))

        supervisor.send = send
        runtime = SessionRuntime(supervisor, reset_transaction_factory=lambda path: None)
        app = Application(name="Voice reset", version="1", security=Security(), plugins=(REALTIME,),
            server_extensions=(ServerExtension(name="runtime", service=runtime),
                               ServerExtension(name="canonical_transport", service=SimpleNamespace())),)
        app = _assemble_server(app, session=runtime)
        api = app.state.application.extensions["realtime"].service
        api.call_id = "client-call"
        api.controller.call_id = "voice-call"

        async def reset(transaction_factory):
            assert transaction_factory is runtime.reset_transaction_factory
            assert api.call_id is None
            assert not api.controller.active
            events.append("worker reset")
            return "ready"

        runtime._reset_canonical_runtime = reset
        relay = asyncio.create_task(runtime.relay())
        resetting = asyncio.create_task(runtime.reset_runtime_state())
        try:
            await asyncio.wait_for(barrier_sent.wait(), 1)
            assert events == ["voice barrier"]
            assert not resetting.done()
            acknowledge.set()
            assert await asyncio.wait_for(resetting, 1) == "ready"
            assert events == ["voice barrier", "worker reset"]
            assert not api.controller.barriers
        finally:
            resetting.cancel()
            relay.cancel()
            await asyncio.gather(resetting, relay, return_exceptions=True)

    asyncio.run(run())
