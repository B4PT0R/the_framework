"""Complete feature declarations stay usable in workers and installed apps."""

import subprocess
import sys
from types import SimpleNamespace

from the_framework import BuildContext
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM


def test_complete_plugin_imports_and_worker_compile_do_not_import_server_modules():
    result = subprocess.run([sys.executable, "-c", '''
import sys
from the_framework import ApplicationSpec, AgentSpec, Plugin
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
plan = ApplicationSpec(name="Example", version="1", plugins=plugins,
    primary_agent=AgentSpec(name="primary", session_mode="durable")).compile()
assert "memory.jiminy" in plan.agents
assert len(plan.primary_agent.extensions) == len(plugins)
for package in ("scheduler", "system", "realtime"):
    assert "the_framework.plugins." + package + ".server" not in sys.modules
assert "the_framework.plugins.realtime.controller" not in sys.modules
assert "the_framework.plugins.realtime.api" not in sys.modules
'''], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_complete_stateful_plugins_install_handlers_and_reset_hooks(tmp_path):
    handlers, observers, hooks = {}, [], []
    runtime = SimpleNamespace(
        application=SimpleNamespace(register=lambda name, handler, **kwargs: handlers.setdefault(name, handler)),
        register_observer=observers.append,
        register_reset_hooks=lambda **kwargs: hooks.append(kwargs),
        submit=lambda command: None,
        supervisor=SimpleNamespace(send=lambda command: None),
    )
    context = BuildContext(
        scheduler_state_path=tmp_path / "custom-clock.json",
        system_state_path=tmp_path / "custom-control.json",
    )
    scheduler = SCHEDULER.build_server_extensions(context)[0].service_factory(runtime)
    system = SYSTEM.build_server_extensions(context)[0].service_factory(runtime)
    assert scheduler.path == tmp_path / "custom-clock.json"
    assert system.path == tmp_path / "custom-control.json"
    assert set(handlers) == {"scheduler", "system"}
    assert len(observers) == len(hooks) == 2
    assert hooks[0] == {"suspend": scheduler.stop, "resume": scheduler.start}
    assert hooks[1]["resume"] == system.start
    assert scheduler.health()["wakes"] == 0
