"""Snapshot of the starter recipe used during composition simplification."""

from starter.application import application

def test_starter_declares_general_plugins_and_memory_specialist():
    plan = application.compile()
    assert plan.agents["assistant"].name == "assistant"
    assert {plugin.name for plugin in application.plugins} == {
        "bash", "registry", "web_search", "memory", "scheduler", "system",
        "realtime", "chromium", "artifacts", "agentic",
    }
    assert "memory.jiminy" in plan.agents
    assert plan.capabilities["realtime.session"].version == 1
