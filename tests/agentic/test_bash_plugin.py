import asyncio
import threading
import time

import pytest

from the_framework.agent import Agent, ShutdownRequest, Worker
from the_framework.plugins.bash import BashPlugin


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
    return agent, agent.add_plugin(BashPlugin)


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
    })
    path = tmp_path / "src" / "example.txt"
    assert path.read_text() == "alpha\nbeta\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert "replaced 1 match" in plugin.edit({
        "path": "src/example.txt",
        "old_string": "beta",
        "new_string": "gamma",
    })
    assert path.read_text() == "alpha\ngamma\n"


def test_bash_accepts_absolute_paths_and_rejects_ambiguous_edits(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    (tmp_path / "duplicate.txt").write_text("same same")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"

    plugin.write({"path": outside, "content": "allowed"})
    assert outside.read_text() == "allowed"
    with pytest.raises(ValueError, match="matched 2 times"):
        plugin.edit({
            "path": "duplicate.txt",
            "old_string": "same",
            "new_string": "different",
        })
    assert (tmp_path / "duplicate.txt").read_text() == "same same"


def test_bash_can_inherit_the_interactive_shell_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    bin_dir = home / "interactive-bin"
    bin_dir.mkdir(parents=True)
    executable = bin_dir / "interactive-command"
    executable.write_text("#!/bin/sh\nprintf inherited")
    executable.chmod(0o700)
    (home / ".bashrc").write_text(f'export PATH="{bin_dir}:$PATH"\n')
    monkeypatch.setenv("HOME", str(home))

    _agent, plugin = bash_plugin(tmp_path, inherit_interactive_environment=True)
    result = plugin.command("interactive-command")

    assert result["exit_code"] == 0
    assert result["stdout"] == "inherited"


def test_bash_executes_command_and_python_with_bounded_output(tmp_path):
    _agent, plugin = bash_plugin(tmp_path, max_output_chars=12)

    command = plugin.command("pwd; printf 123456789012345", timeout_seconds=None)
    python = plugin.python("print(6 * 7)", timeout_seconds=2)

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
    timed_out = plugin.command("sleep 2", timeout_seconds=1)
    assert timed_out["timed_out"] is True
    assert timed_out["status"] == "timed_out"
    assert timed_out["exit_code"] < 0

    result = []
    thread = threading.Thread(
        target=lambda: result.append(plugin.command("sleep 30", timeout_seconds=None)),
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not plugin._processes and time.monotonic() < deadline:
        time.sleep(.01)
    agent.interrupt("test")
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result[0]["exit_code"] < 0
    assert result[0]["status"] == "interrupted"
    assert plugin._processes == set()


def test_bash_job_check_wait_and_interrupt_lifecycle(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)

    started = plugin.job(
        "printf start; sleep .2; printf end",
        timeout_seconds=None,
    )

    assert started["status"] == "started"
    current = plugin.check(started["job_id"])
    assert current["status"] in {"running", "completed"}
    completed = plugin.wait(started["job_id"], timeout_seconds=2)
    assert completed["status"] == "completed"
    assert completed["exit_code"] == 0
    assert completed["stdout"] == "startend"
    assert plugin.check(started["job_id"])["status"] == "completed"

    long_job = plugin.job("sleep 30", timeout_seconds=None)
    interrupted = plugin.interrupt(long_job["job_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["exit_code"] < 0
    assert plugin.check(long_job["job_id"])["status"] == "interrupted"


def test_bash_wait_can_idle_without_ending_the_agent_turn(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep .2; printf ready", timeout_seconds=None)

    still_running = plugin.wait(started["job_id"], timeout_seconds=0)
    assert still_running["status"] == "running"
    completed = plugin.wait(started["job_id"], timeout_seconds=2)

    assert completed["status"] == "completed"
    assert completed["stdout"] == "ready"


def test_bash_wait_wakes_for_steering_without_stopping_the_job(tmp_path):
    agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep 2; printf ready", timeout_seconds=None)
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(plugin.wait(started["job_id"], timeout_seconds=3))
    )
    waiter.start()
    time.sleep(0.05)

    agent.agentic_loop.queue_steering("new user request")
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert result[0]["wait_reason"] == "steering"
    assert result[0]["status"] == "running"
    assert plugin.check(started["job_id"])["status"] == "running"
    plugin.interrupt(started["job_id"])


def test_bash_job_hard_timeout_applies_while_unobserved(tmp_path):
    _agent, plugin = bash_plugin(tmp_path)
    started = plugin.job("sleep 2", timeout_seconds=1)

    completed = plugin.wait(started["job_id"], timeout_seconds=2)

    assert completed["status"] == "timed_out"
    assert completed["timed_out"] is True


def test_worker_shutdown_terminates_uncollected_bash_processes(tmp_path):
    async def scenario():
        agent, plugin = bash_plugin(tmp_path)
        started = plugin.job("sleep 30", timeout_seconds=None)
        incoming = asyncio.Queue()
        outgoing = []

        async def send(output):
            outgoing.append(output)

        worker = Worker(agent, incoming.get, send)
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        assert started["status"] == "started"
        assert plugin._processes == set()
        assert plugin._jobs == {}
        assert outgoing[-1].type == "worker_stopped"

    asyncio.run(scenario())
