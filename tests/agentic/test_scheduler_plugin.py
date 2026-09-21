import asyncio
from datetime import datetime, timedelta, timezone

from core.agent import Agent

from core.plugins.scheduler import SchedulerPlugin


def iso_after(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def test_scheduler_plugin_calls_the_server_owned_application_capability():
    async def test():
        agent = Agent(client=object())
        calls = []

        async def request(payload):
            calls.append(payload)
            return {"id": payload.payload["wake_id"], "status": "created"}

        agent.application.bind(request)
        plugin = agent.add_plugin(SchedulerPlugin)
        result = await plugin.create_wake(
            "Morning hello",
            "Wish Baptiste a gentle morning.",
            at=iso_after(60),
        )

        assert result["status"] == "created"
        assert calls[0].capability == "scheduler"
        assert calls[0].method == "create"
        assert calls[0].payload["max_wakes"] == 64
        assert calls[0].payload["min_interval_minutes"] == 1

    asyncio.run(test())


def test_scheduler_plugin_is_loaded_and_activated_with_agent_config():
    agent = Agent(client=object(), scheduler={"max_wakes": 12})
    plugin = agent.add_plugin(SchedulerPlugin)

    assert plugin.activated is True
    assert plugin.config.max_wakes == 12
    assert agent.registry("tools")["scheduler"] is not None
