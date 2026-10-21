"""Agent observation lifetime is independent of the persistent browser."""
import asyncio
from unittest.mock import Mock

import pytest

from the_framework.agent import Agent
from the_framework.plugins.browser import ChromiumExtension
from the_framework.server.clients.browser import BrowserError


@pytest.fixture
def plugin(tmp_path):
    extension = ChromiumExtension(Agent(client=object()), runtime_root=tmp_path)
    extension.controller = Mock()
    extension.controller.session_call.return_value = {"open": False, "tabs": []}
    return extension


def test_observation_starts_closed_and_empty_open_does_not_launch_browser(plugin):
    assert asyncio.run(plugin.chromium_state()) is None
    plugin.controller.session_call.assert_not_called()
    plugin.controller.call.assert_not_called()
    result = plugin.open_session("web")
    assert result["tabs"] == [] and result["open"] is False
    assert asyncio.run(plugin.chromium_state()) == {
        "target": "web", "observation_open": True, "open": False, "tabs": [],
    }
    plugin.controller.call.assert_not_called()
    plugin.controller.reset_mock()
    plugin.close_session()
    assert asyncio.run(plugin.chromium_state()) is None
    plugin.controller.session_call.assert_not_called()
    plugin.controller.call.assert_not_called()
    assert plugin.close_session()["target"] is None


def test_resume_reads_existing_tabs_without_changing_them(plugin):
    plugin.open_session("web")
    plugin.close_session()
    plugin.controller.session_call.return_value = {
        "open": True, "tabs": [{"index": 0, "url": "https://user.example/new"}],
    }
    result = plugin.open_session("web")
    assert result["tabs"][0]["url"] == "https://user.example/new"
    plugin.controller.call.assert_not_called()
    plugin._provider_observation = Mock(return_value="fresh DOM and image")
    assert asyncio.run(plugin.chromium_state()) == "fresh DOM and image"
    plugin._provider_observation.assert_called_once_with("web")
    plugin.close_session()
    assert asyncio.run(plugin.chromium_state()) is None
    plugin._provider_observation.assert_called_once()


@pytest.mark.parametrize("method,args", [
    ("status", ()), ("open_web", ()), ("close_tab", ()), ("close_web", ()),
    ("goto", ("https://example.com",)), ("back", ()), ("select_tab", (0,)),
    ("click", ("id",)), ("fill", ("id", "text")),
    ("select", ("id", "value")), ("press", ("Enter",)),
])
def test_actions_require_explicit_observation_session(plugin, method, args):
    with pytest.raises(BrowserError, match="open_session"):
        getattr(plugin, method)(*args)
    plugin.controller.call.assert_not_called()
    plugin.controller.session_call.assert_not_called()


@pytest.mark.parametrize("method,args", [
    ("open_web", ()), ("close_tab", ()), ("close_web", ()),
    ("goto", ("https://example.com",)),
])
def test_web_operations_cannot_implicitly_switch_from_ui(plugin, method, args):
    plugin.open_session("harness_ui")
    plugin.controller.reset_mock()
    with pytest.raises(BrowserError, match="target='web'"):
        getattr(plugin, method)(*args)
    assert plugin.active_target == "harness_ui"
    plugin.controller.call.assert_not_called()
    plugin.controller.session_call.assert_not_called()


def test_ui_actions_follow_explicit_session_and_invalid_target_keeps_it(plugin):
    plugin.open_session("harness_ui")
    plugin.click("id")
    assert plugin.controller.session_call.call_args_list[-2].args == (
        "harness_ui", "click", "id",
    )
    with pytest.raises(BrowserError):
        plugin.open_session("invalid")
    assert plugin.active_target == "harness_ui"
    plugin.close_session()
    assert asyncio.run(plugin.chromium_state()) is None


def test_external_navigation_does_not_enable_observation(plugin):
    plugin.on_external_browser_open_web({"url": "https://example.com"})
    plugin.on_external_browser_open_ui({"url": "http://localhost/ui"})
    assert plugin.active_target is None
    assert asyncio.run(plugin.chromium_state()) is None
    plugin.controller.session_call.assert_not_called()
