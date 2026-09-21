import pytest

from the_framework.plugins.web_search import WebSearchPlugin
from the_framework.agent import Agent


def test_web_search_plugin_exposes_configured_hosted_tool():
    agent = Agent(
        client=object(),
        web_search={
            "search_context_size": "high",
            "external_web_access": False,
        }
    )

    plugin = agent.add_plugin(WebSearchPlugin)
    tool = agent.registry("tools")["web_search"]

    assert plugin.activated is True
    assert tool == {
        "type": "web_search",
        "search_context_size": "high",
        "external_web_access": False,
    }
    assert plugin.instructions_section().content in agent.context.instructions()


def test_web_search_plugin_rejects_invalid_context_size():
    agent = Agent(
        client=object(),
        web_search={"search_context_size": "enormous"},
    )

    with pytest.raises(ValueError, match="must be low, medium, or high"):
        agent.add_plugin(WebSearchPlugin)


def test_web_search_plugin_is_inert_when_not_activated():
    agent = Agent(client=object())

    plugin = agent.add_plugin(WebSearchPlugin, activate=False)

    assert plugin.activated is False
    assert "web_search" not in agent.registry("tools")
