import asyncio

import pytest

from the_framework.agent import Agent
from the_framework.agent.extensions.hooks import Hooks


def test_async_hook_preserves_payload_and_supports_sync_hooks():
    hooks = Hooks()

    async def asynchronous(payload):
        await asyncio.sleep(0)
        payload.append("async")

    def synchronous(payload):
        return [*payload, "sync"]

    hooks.add(asynchronous)
    hooks.add(synchronous)
    payload = []
    assert asyncio.run(hooks.arun("missing", payload)) is payload
    assert asyncio.run(hooks.arun("asynchronous", payload)) is payload
    assert asyncio.run(hooks.arun("synchronous", payload)) == ["async", "sync"]


@pytest.mark.parametrize("fails", [False, True])
def test_after_generation_hook_runs_on_worker_loop_after_stream(fails):
    agent = Agent(client=object())
    order = []

    async def after_generation(payload):
        # Unlike the synchronous response-stream hook, this boundary can await
        # the application bridge without creating another event loop/thread.
        order.append(("hook", asyncio.get_running_loop()))
        await asyncio.sleep(0)

    def generate(payload):
        order.append(("generation", None))
        if fails:
            raise RuntimeError("stream failed")

    async def scenario():
        loop = asyncio.get_running_loop()
        agent.hooks.add(after_generation)
        agent.agentic_loop.generation = generate
        if fails:
            with pytest.raises(RuntimeError, match="stream failed"):
                await agent.agentic_loop.step()
        else:
            await agent.agentic_loop.step()
        assert order == [("generation", None), ("hook", loop)]

    asyncio.run(scenario())
