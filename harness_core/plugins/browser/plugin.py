import json
from pathlib import Path

from harness_core.agent import Config, Image, Plugin, hook, provider, tool

from ...server.clients.browser import BrowserController, BrowserService, validate_target
from ...server.clients.browser_rpc import BrowserRPCClient, browser_socket_path


class BrowserConfig(Config):
    web_headless: bool = False
    ui_headless: bool = False
    ui_app_mode: bool = True
    viewport_width: int = 1365
    viewport_height: int = 900
    max_snapshot_elements: int = 120


class ChromiumPlugin(Plugin):
    name = "chromium"
    description = "Inspect and interact with isolated Playwright Chromium targets."
    config = BrowserConfig
    instructions_file = Path(__file__).with_name("instructions.md")

    def __init__(self, agent, *, runtime_root, service_factory=None):
        super().__init__(agent)
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.service_factory = service_factory
        self.active_target = "web"
        self._owns_controller = service_factory is not None
        self.controller = (
            BrowserController(self._service)
            if self._owns_controller
            else BrowserRPCClient(browser_socket_path(self.runtime_root))
        )

    def _service(self):
        if self.service_factory is not None:
            return self.service_factory()
        return BrowserService(
            self.runtime_root,
            web_headless=self.config.web_headless,
            ui_headless=self.config.ui_headless,
            ui_app_mode=self.config.ui_app_mode,
            viewport={
                "width": self.config.viewport_width,
                "height": self.config.viewport_height,
            },
        )

    def deactivate(self):
        if self._owns_controller:
            self.controller.close()
        return super().deactivate()

    def shutdown(self):
        if self._owns_controller:
            self.controller.close()

    @hook
    def on_external_browser_open_ui(self, payload):
        """Open or foreground the authenticated Harness UI bootstrap URL."""
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError("browser UI event requires a URL")
        self.controller.call("open_harness_ui", url)
        return payload

    @hook
    def on_external_browser_open_web(self, payload):
        """Open an external HTTP(S) URL in the isolated web target."""
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("browser web event requires an HTTP(S) URL")
        self.controller.call("open_web", url, new_tab=True)
        return payload

    def _activate(self, target):
        self.active_target = validate_target(target)
        return self.active_target

    def _status(self, target, *, action):
        target = self._activate(target)
        status = self.controller.session_call(target, "status")
        tabs = status.get("tabs") or []
        active = next((tab for tab in tabs if tab.get("active")), None)
        return {
            "status": "success",
            "action": action,
            "target": target,
            "active_tab": active,
            "tab_count": len(tabs),
        }

    def _provider_observation(self, target):
        target = validate_target(target)
        path = (
            self.runtime_root
            / "browser"
            / "screenshots"
            / target
            / "active_tab.png"
        )
        snapshot, screenshot = self.controller.call(
            "observe",
            target,
            path,
            max_elements=self.config.max_snapshot_elements,
        )
        return (
            self._format_snapshot(target, snapshot),
            Image(
                path=str(screenshot),
                description=f"Current Playwright screenshot of the {target} target.",
                parameters={
                    "target": target,
                    "url": snapshot.get("url"),
                    "title": snapshot.get("title"),
                },
                context_tag="browser_screenshot",
            ),
        )

    @staticmethod
    def _format_snapshot(target, snapshot):
        lines = [
            f"Target: {target}",
            f"Page: {snapshot.get('title') or '(untitled)'}",
            f"URL: {snapshot.get('url') or ''}",
            "",
            "Elements:",
        ]
        elements = snapshot.get("elements") or []
        if not elements:
            lines.append("  (no useful visible elements found)")
        for element in elements:
            role = element.get("role") or "element"
            name = element.get("name") or element.get("text") or ""
            parts = [f"[{element.get('id')}]", role]
            if name:
                parts.append(json.dumps(name, ensure_ascii=False))
            details = []
            if element.get("href"):
                details.append(f"href={element['href']}")
            if element.get("placeholder"):
                details.append(
                    f"placeholder={json.dumps(element['placeholder'], ensure_ascii=False)}"
                )
            if element.get("value") and role in {
                "input", "textarea", "select", "search", "textbox",
            }:
                details.append(
                    f"value={json.dumps(element['value'], ensure_ascii=False)}"
                )
            if element.get("checked"):
                details.append("checked")
            if element.get("disabled"):
                details.append("disabled")
            suffix = f" ({'; '.join(details)})" if details else ""
            lines.append("  " + " ".join(parts) + suffix)
        if snapshot.get("truncated"):
            lines.append(
                "  ... snapshot truncated; narrow the page or raise the configured limit"
            )
        return "\n".join(lines)

    @provider
    def chromium_state(self):
        """Expose the active Chromium target as fresh ephemeral context."""
        target = self.active_target
        try:
            status = self.controller.session_call(target, "status")
            if not status.get("open") or not status.get("tabs"):
                return None
            return self._provider_observation(target)
        except Exception as error:
            return {
                "target": target,
                "error": f"Current Chromium state is unavailable: {error}",
            }

    @tool
    def observe(self, target: str = "web"):
        """Select a browser target for fresh provider observation on the next step."""
        return self._status(target, action="observe")

    @tool
    def status(self, target: str = "web"):
        """List tabs and active state for one isolated browser target."""
        self._activate(target)
        return {"target": target, **self.controller.session_call(target, "status")}

    @tool
    def open_web(self, url: str | None = None, new_tab: bool = False):
        """Open the web Chromium target, optionally navigating or creating a tab."""
        self.controller.call("open_web", url, new_tab=new_tab)
        return self._status("web", action="open_web")

    @tool
    def close_tab(self, index: int | None = None):
        """Close one web tab by index, or the active web tab when omitted."""
        self._activate("web")
        result = self.controller.call("close_web_tab", index)
        return {"status": "success", "action": "close_tab", "target": "web", **result}

    @tool
    def close_web(self):
        """Close the arbitrary web navigation session without closing the Harness UI."""
        self._activate("web")
        result = self.controller.call("close_web")
        return {"status": "success", "action": "close_web", "target": "web", **result}

    @tool
    def goto(self, url: str):
        """Navigate the web target; arbitrary navigation is unavailable to harness_ui."""
        self.controller.call("goto", "web", url)
        return self._status("web", action="goto")

    @tool
    def back(self, target: str = "web"):
        """Navigate the active tab of one target backward."""
        validate_target(target)
        self.controller.session_call(target, "back")
        return self._status(target, action="back")

    @tool
    def select_tab(self, index: int, target: str = "web"):
        """Select a zero-based tab in one browser target."""
        validate_target(target)
        self.controller.session_call(target, "select_tab", index)
        return self._status(target, action="select_tab")

    @tool
    def click(self, element_id: str, target: str = "web"):
        """Click an element ID from the latest observation of one target."""
        validate_target(target)
        self.controller.session_call(target, "click", element_id)
        return self._status(target, action="click")

    @tool
    def fill(self, element_id: str, text: str, target: str = "web"):
        """Fill an editable element ID from the latest observation of one target."""
        validate_target(target)
        self.controller.session_call(target, "fill", element_id, text)
        return self._status(target, action="fill")

    @tool
    def select(self, element_id: str, value: str | list[str], target: str = "web"):
        """Select one or more values in a select element from the latest observation."""
        validate_target(target)
        self.controller.session_call(target, "select", element_id, value)
        return self._status(target, action="select")

    @tool
    def press(self, key: str, element_id: str | None = None, target: str = "web"):
        """Press a Playwright key globally or on an observed element ID."""
        validate_target(target)
        self.controller.session_call(target, "press", key, element_id)
        return self._status(target, action="press")
