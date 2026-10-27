import asyncio
import json
import os
import signal
import sys
import threading
import time

import pytest

from the_framework.agent import Agent, ShutdownRequest, Worker
from the_framework.plugins.bash import BashExtension


@pytest.fixture(autouse=True)
def close_bash_workers(monkeypatch):
    plugins = []
    original = BashExtension.__init__

    def create(self, agent):
        original(self, agent)
        plugins.append(self)

    monkeypatch.setattr(BashExtension, "__init__", create)
    yield
    for plugin in plugins:
        plugin.shutdown()


def bash_plugin(tmp_path, **overrides):
    config = {
        "default_cwd": str(tmp_path),
        "inherit_interactive_environment": False,
        "max_timeout_seconds": 10,
        "default_wait_seconds": 1,
        "max_wait_seconds": 3,
        "max_output_chars": 1_000,
        **overrides,
    }
    agent = Agent(client=object(), bash=config)
    return agent, agent.add_extension(BashExtension)


def test_bash_plugin_exposes_the_complete_agentic_surface(tmp_path):
    agent, plugin = bash_plugin(tmp_path)
    namespace = agent.registry("tools")["bash"]

    assert plugin.activated is True
    assert {tool.name for tool in namespace.tools} == {
        "write", "edit", "command", "job", "check", "wait",
        "interrupt", "python",
    }
    assert plugin.instructions_section().content.strip()


def test_bash_batch_file_schemas_are_recursively_strict(tmp_path):
    agent, _plugin = bash_plugin(tmp_path)
    namespace = agent.registry("tools")["bash"]

    for tool_name, parameter_name in (("write", "writes"), ("edit", "edits")):
        tool = namespace.find(tool_name)
        batch = tool.parameters.properties[parameter_name]
        assert batch.type == "array"
        assert batch["items"]["type"] == "object"
        assert batch["items"]["additionalProperties"] is False
        assert set(batch["items"]["required"]) == set(batch["items"]["properties"])


def test_bash_writes_and_edits_atomically(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)

    assert "wrote 11 characters" in plugin.write({
        "path": "src/example.txt",
        "content": "alpha\nbeta\n",
    }, target="local")
    path = tmp_path / "src" / "example.txt"
    assert path.read_text() == "alpha\nbeta\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "replaced 1 match" in plugin.edit({
        "path": "src/example.txt",
        "old_string": "beta",
        "new_string": "gamma",
    }, target="local")
    assert path.read_text() == "alpha\ngamma\n"


def test_bash_accepts_absolute_paths_and_rejects_ambiguous_edits(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    (tmp_path / "duplicate.txt").write_text("same same")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"

    plugin.write({"path": outside, "content": "allowed"}, target="local")
    assert outside.read_text() == "allowed"
    with pytest.raises(ValueError, match="matched 2 times"):
        plugin.edit({
            "path": "duplicate.txt",
            "old_string": "same",
            "new_string": "different",
        }, target="local")
    assert (tmp_path / "duplicate.txt").read_text() == "same same"


def test_bash_can_inherit_the_interactive_shell_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bin_dir = home / "interactive-bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "interactive-command"
    executable.write_text("#!/bin/sh\nprintf inherited")
    executable.chmod(0o700)
    (home / ".bashrc").write_text(
        f'export PATH="{bin_dir}:$PATH"\nexport BASH_TEST_ENV=inherited\n'
    )
    monkeypatch.setenv("HOME", str(home))

    _agent, plugin = bash_plugin(tmp_path, inherit_interactive_environment=True)
    # Resolve the executable without running it: /tmp may legitimately be noexec.
    result = plugin.command('command -v interactive-command; printf "$BASH_TEST_ENV"', target="local")

    assert result["exit_code"] == 0
    assert result["stdout"] == f"{executable}\ninherited"


def test_bash_executes_command_and_python_with_bounded_output(tmp_path):
    _agent, plugin = bash_plugin(tmp_path, max_output_chars=12)

    command = plugin.command("pwd; printf 123456789012345", timeout_seconds=None, target="local")
    python = plugin.python("print(6 * 7)", timeout_seconds=2, target="local")

    assert command["exit_code"] == 0
    assert command["status"] == "completed"
    assert command["cwd"] == str(tmp_path)
    assert "truncated" in command["stdout"]
    assert python == {
        "exit_code": 0,
        "stdout": "42\n",
        "stderr": "",
        "timed_out": False,
        "cwd": str(tmp_path),
    }


def test_bash_timeout_and_agent_interrupt_terminate_process_groups(tmp_path):
    agent, plugin = bash_plugin(tmp_path)
    timed_out = plugin.command("sleep 2", timeout_seconds=1, target="local")
    assert timed_out["timed_out"] is True
    assert timed_out["status"] == "timed_out"
    assert timed_out["exit_code"] < 0

    result = []
    thread = threading.Thread(
        target=lambda: result.append(plugin.command("printf started > ready; sleep 30", timeout_seconds=None, target="local")),
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert (tmp_path / "ready").exists()
    agent.interrupt("test")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result[0]["exit_code"] < 0
    assert result[0]["status"] == "interrupted"


def test_bash_job_check_wait_and_interrupt_lifecycle(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)

    started = plugin.job(
        "printf start; sleep .2; printf end",
        timeout_seconds=None,
     target="local")

    assert started["status"] == "started"
    current = plugin.check(started["job_id"], target="local")
    assert current["status"] in {"running", "completed"}
    completed = plugin.wait(started["job_id"], timeout_seconds=2, target="local")
    assert completed["status"] == "completed"
    assert completed["exit_code"] == 0
    assert completed["stdout"] == "startend"
    assert plugin.check(started["job_id"], target="local")["status"] == "completed"

    long_job = plugin.job("sleep 30", timeout_seconds=None, target="local")
    interrupted = plugin.interrupt(long_job["job_id"], target="local")
    assert interrupted["status"] == "interrupted"
    assert interrupted["exit_code"] < 0
    assert plugin.check(long_job["job_id"], target="local")["status"] == "interrupted"


def test_bash_wait_can_idle_without_ending_the_agent_turn(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep .2; printf ready", timeout_seconds=None, target="local")

    still_running = plugin.wait(started["job_id"], timeout_seconds=0, target="local")
    assert still_running["status"] == "running"
    completed = plugin.wait(started["job_id"], timeout_seconds=2, target="local")

    assert completed["status"] == "completed"
    assert completed["stdout"] == "ready"


def test_bash_wait_wakes_for_steering_without_stopping_the_job(tmp_path):
    agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep 2; printf ready", timeout_seconds=None, target="local")
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(plugin.wait(started["job_id"], timeout_seconds=3, target="local"))
    )
    waiter.start()
    time.sleep(0.05)

    agent.agentic_loop.queue_steering("new user request")
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert result[0]["wait_reason"] == "steering"
    assert result[0]["status"] == "running"
    assert plugin.check(started["job_id"], target="local")["status"] == "running"
    plugin.interrupt(started["job_id"], target="local")


def test_bash_job_hard_timeout_applies_while_unobserved(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep 2", timeout_seconds=1, target="local")

    completed = plugin.wait(started["job_id"], timeout_seconds=2, target="local")

    assert completed["status"] == "timed_out"
    assert completed["timed_out"] is True


def test_worker_shutdown_terminates_uncollected_bash_processes(tmp_path):
    async def scenario():
        agent, plugin = bash_plugin(tmp_path)
        started = plugin.job("sleep 30", timeout_seconds=None, target="local")
        incoming = asyncio.Queue()
        outgoing = []

        async def send(output):
            outgoing.append(output)

        worker = Worker(agent, incoming.get, send)
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        assert started["status"] == "started"
        assert plugin._clients == {}
        assert outgoing[-1].type == "worker_stopped"

    asyncio.run(scenario())


def test_bash_target_is_required_and_host_never_falls_back(tmp_path, monkeypatch):
    agent, plugin = bash_plugin(tmp_path)
    namespace = agent.registry("tools")["bash"]
    for entry in namespace.tools:
        assert "target" in entry.parameters.required
        assert set(entry.parameters.properties.target.enum) == {"local", "host"}
    with pytest.raises(TypeError, match="target"):
        plugin.command("touch must-not-exist")
    monkeypatch.delenv("THE_FRAMEWORK_HOST_SETTINGS", raising=False)
    with pytest.raises(RuntimeError, match="host execution is not configured"):
        plugin.command("touch must-not-exist", target="host")
    assert not (tmp_path / "must-not-exist").exists()
    assert not plugin._clients


@pytest.mark.parametrize("settings", [{"validation": True}, {"host": None}])
def test_bash_host_execution_disabled_for_validation(tmp_path, monkeypatch, settings):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(settings))
    monkeypatch.setenv("THE_FRAMEWORK_HOST_SETTINGS", str(path))
    _agent, plugin = bash_plugin(tmp_path)
    with pytest.raises(RuntimeError, match="unavailable in this runtime"):
        plugin.command("touch must-not-exist", target="host")
    assert not (tmp_path / "must-not-exist").exists()


def test_bash_live_output_is_visible_before_completion(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("printf ready; sleep 30", target="local")
    deadline = time.monotonic() + 3
    while True:
        result = plugin.check(started["job_id"], target="local")
        if result["stdout"] == "ready" or time.monotonic() >= deadline:
            break
        time.sleep(.01)
    assert result["status"] == "running"
    assert result["stdout"] == "ready"
    plugin.interrupt(started["job_id"], target="local")


def test_bash_worker_close_reaps_running_job(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("echo $$; exec sleep 30", target="local")
    deadline = time.monotonic() + 3
    while True:
        result = plugin.check(started["job_id"], target="local")
        if result["stdout"] or time.monotonic() >= deadline:
            break
        time.sleep(.01)
    pid = int(result["stdout"])
    worker = plugin._clients["local"].process
    plugin.shutdown()
    assert worker.poll() == 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_bash_worker_termination_reaps_running_job(tmp_path, number):
    _agent, plugin = bash_plugin(tmp_path)
    job = plugin.job("echo $$; exec sleep 30", target="local")
    deadline = time.monotonic() + 3
    while True:
        result = plugin.check(job["job_id"], target="local")
        if result["stdout"]:
            break
        assert time.monotonic() < deadline
        time.sleep(.01)
    pid = int(result["stdout"])
    process = plugin._clients["local"].process
    try:
        process.send_signal(number)
        assert process.wait(timeout=5) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        # Never leave a test-owned child behind if cleanup regresses.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_bash_worker_crash_does_not_silently_recreate_jobs(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    plugin.command("true", target="local")
    client = plugin._clients["local"]
    client.process.send_signal(signal.SIGKILL)
    client.process.wait(timeout=3)
    with pytest.raises((ConnectionError, OSError)):
        plugin.command("touch must-not-exist", target="local")
    assert plugin._clients["local"] is client
    assert not (tmp_path / "must-not-exist").exists()


def test_bash_host_and_local_workers_have_separate_environments_and_jobs(tmp_path, monkeypatch):
    from the_framework.deployment.host import HostEndpoint, HostServices
    from the_framework.execution import ProcessHost

    monkeypatch.setenv("BASH_TEST_TARGET", "local")

    def host_recipe(fd):
        # This is registered on the host, never supplied by the client protocol.
        return [sys.executable, "-c", (
            "import os,runpy; os.environ['BASH_TEST_TARGET']='host'; "
            "runpy.run_module('the_framework.plugins.bash.worker',run_name='__main__')"
        ), str(fd)]

    async def scenario():
        host = ProcessHost({"bash": host_recipe})
        services = HostServices(object(), token="control", browser_token="browser",
                                execution=host, execution_token="execution")
        address = await services.start(("127.0.0.1", 0))
        endpoint = HostEndpoint(address, "control", "browser", "execution")
        settings = tmp_path / "runtime.json"
        settings.write_text(json.dumps({"host": endpoint.settings()}))
        monkeypatch.setenv("THE_FRAMEWORK_HOST_SETTINGS", str(settings))
        agent, plugin = bash_plugin(tmp_path)

        def exercise():
            for target in ("local", "host"):
                result = plugin.python("import os; print(os.environ['BASH_TEST_TARGET'])", target=target)
                assert result["stdout"].strip() == target
                plugin.write({"path": target, "content": "old"}, target=target)
                plugin.edit({"path": target, "old_string": "old", "new_string": "new"}, target=target)
                assert (tmp_path / target).read_text() == "new"
            job = plugin.job("sleep 30", target="host")
            with pytest.raises(ValueError, match="unknown or expired job"):
                plugin.check(job["job_id"], target="local")
            assert plugin.interrupt(job["job_id"], target="host")["status"] == "interrupted"
            assert plugin.check(job["job_id"], target="host")["status"] == "interrupted"
            # Agent interruption is a separate control request, not queued behind
            # a blocking tool request on the duplex channel.
            result = []
            thread = threading.Thread(target=lambda: result.append(plugin.command(
                "printf ready > host-ready; sleep 30", target="host")))
            thread.start()
            deadline = time.monotonic() + 3
            while not (tmp_path / "host-ready").exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert (tmp_path / "host-ready").exists()
            agent.interrupt("host test")
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert result[0]["status"] == "interrupted"

        try:
            await asyncio.to_thread(exercise)
        finally:
            await asyncio.to_thread(plugin.shutdown)
            await services.close()
        assert not host.processes
        assert not services.connections

    asyncio.run(scenario())


def test_shutdown_attempts_both_targets_after_a_host_error(tmp_path):
    from types import SimpleNamespace

    _, plugin = bash_plugin(tmp_path)
    closed = []

    def fail():
        closed.append('host')
        raise ConnectionError('host disconnected')

    plugin._clients = {
        'host': SimpleNamespace(close=fail),
        'local': SimpleNamespace(close=lambda: closed.append('local')),
    }
    with pytest.raises(ExceptionGroup, match='Bash target cleanup failed'):
        plugin.shutdown()
    assert closed == ['host', 'local'] and not plugin._clients
