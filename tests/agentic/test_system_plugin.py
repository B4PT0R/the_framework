import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

from core.agent import Agent, AgenticLoop
from core.plugins.system import SystemPlugin


def test_system_wait_exposes_a_general_preemptible_idle_tool():
    agent = Agent(
        client=object(),
        system={"default_wait_seconds": 1, "max_wait_seconds": 3},
    )
    plugin = agent.add_plugin(SystemPlugin)
    namespace = agent.registry("tools")["system"]

    assert namespace.find("wait") is not None
    assert plugin.wait(0)["status"] == "elapsed"

    result = []
    waiter = threading.Thread(target=lambda: result.append(plugin.wait(3)))
    waiter.start()
    time.sleep(0.05)
    agent.agentic_loop.queue_steering("new direction")
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert result[0]["status"] == "steering"
    assert result[0]["waited_seconds"] < 1


def test_system_wait_reacts_to_turn_interrupt():
    agent = Agent(client=object())
    plugin = agent.add_plugin(SystemPlugin)
    result = []
    waiter = threading.Thread(target=lambda: result.append(plugin.wait(3)))
    waiter.start()
    time.sleep(0.05)

    agent.interrupt("stop waiting")
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert result[0]["status"] == "interrupt"


def test_system_plugin_exposes_current_local_datetime_as_ephemeral_context(monkeypatch):
    instant = datetime(
        2026,
        8,
        14,
        15,
        42,
        7,
        tzinfo=timezone(timedelta(hours=2), "CEST"),
    )
    monkeypatch.setattr(SystemPlugin, "_now", staticmethod(lambda: instant))
    agent = Agent(client=object())
    agent.add_plugin(SystemPlugin)

    outputs = agent.registry("providers")["current_datetime"].outputs()

    assert len(outputs) == 1
    text = outputs[0].content[0].text
    assert '"date":"2026-08-14"' in text
    assert '"time":"15:42:07+02:00"' in text
    assert '"weekday":"Friday"' in text
    assert '"timezone":"CEST"' in text
    assert '"utc_offset":"+02:00"' in text


def test_graceful_end_turn_prevents_another_agent_step():
    async def test():
        agent = Agent(client=object())
        loop = AgenticLoop(agent)
        steps = []

        async def step():
            steps.append(len(steps) + 1)
            agent.end_turn()
            loop.request_next_step = True

        loop.step = step
        await loop.turn()

        assert steps == [1]
        assert agent.end_of_turn_requested is True

    asyncio.run(test())


def test_system_plugin_requests_reboot_then_gracefully_ends_the_turn():
    async def test():
        agent = Agent(client=object())
        calls = []

        async def request(payload):
            calls.append(payload)
            return {
                "status": "server_restart_requested",
                "action_id": payload.payload["action_id"],
            }

        agent.application.bind(request)
        plugin = agent.add_plugin(SystemPlugin)
        result = await plugin.reboot_server("Relis les changements puis lance les tests.")

        assert result["status"] == "server_restart_requested"
        assert calls[0].capability == "system"
        assert calls[0].method == "reboot_server"
        assert calls[0].payload["resume_instruction"].startswith("Relis")
        assert agent.end_of_turn_requested is True

    asyncio.run(test())


def test_system_plugin_builds_publishes_and_rolls_back_surfaces():
    async def test():
        agent = Agent(client=object())
        calls = []

        async def request(payload):
            calls.append(payload)
            return {"release": "release-1"}

        agent.application.bind(request)
        plugin = agent.add_plugin(SystemPlugin)
        built = await plugin.build_surface()
        published = await plugin.publish_surface(release="release-1")
        rolled_back = await plugin.rollback_surface()

        assert built == published == rolled_back == {"release": "release-1"}
        assert [call.capability for call in calls] == ["surfaces"] * 3
        assert [call.method for call in calls] == ["build", "publish", "rollback"]
        assert calls[0].payload == {"name": "main"}
        assert calls[1].payload == {"name": "main", "release": "release-1"}
        assert calls[2].payload == {"name": "main"}
        assert agent.end_of_turn_requested is False

    asyncio.run(test())


def test_failed_reboot_request_does_not_end_the_turn():
    async def test():
        agent = Agent(client=object())

        async def request(payload):
            raise RuntimeError("restart unavailable")

        agent.application.bind(request)
        plugin = agent.add_plugin(SystemPlugin)
        try:
            await plugin.reboot_server()
        except RuntimeError as error:
            assert str(error) == "restart unavailable"
        else:
            raise AssertionError("failed reboot request was accepted")

        assert agent.end_of_turn_requested is False

    asyncio.run(test())
