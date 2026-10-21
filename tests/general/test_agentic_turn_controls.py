import asyncio
import threading
import time

from the_framework.agent import Agent, AgenticLoop
from the_framework.plugins.agentic import AgenticExtension


def test_agentic_wait_exposes_a_general_preemptible_idle_tool():
    agent = Agent(
        client=object(),
        agentic={"default_wait_seconds": 1, "max_wait_seconds": 3},
    )
    plugin = agent.add_extension(AgenticExtension)
    namespace = agent.registry("tools")["agentic"]

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


def test_agentic_wait_reacts_to_turn_interrupt():
    agent = Agent(client=object())
    plugin = agent.add_extension(AgenticExtension)
    result = []
    waiter = threading.Thread(target=lambda: result.append(plugin.wait(3)))
    waiter.start()
    time.sleep(0.05)

    agent.interrupt("stop waiting")
    waiter.join(timeout=1)

    assert not waiter.is_alive()
    assert result[0]["status"] == "interrupt"


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


