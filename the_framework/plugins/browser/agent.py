import asyncio
import json
from pathlib import Path

from the_framework.agent import Config, Image, AgentExtension, hook, provider, tool

from ...server.clients.browser import BrowserController, BrowserError, BrowserService, validate_target
from ...server.clients.browser_rpc import BrowserRPCClient, browser_socket_path


class BrowserConfig(Config):
    web_headless: bool = False
    ui_headless: bool = False
    ui_app_mode: bool = True
    viewport_width: int = 1365
    viewport_height: int = 900
    max_snapshot_elements: int = 120


class ChromiumExtension(AgentExtension):
    name = "chromium"
    description = "Inspect and interact with isolated Playwright Chromium targets."
    config = BrowserConfig
    instructions_file = Path(__file__).with_name("instructions.md")

    def __init__(self, agent, *, runtime_root, service_factory=None):
        super().__init__(agent)
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.service_factory = service_factory
        self.active_target: str | None = None
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
        self.active_target = None
        if self._owns_controller:
            self.controller.close()
        return super().deactivate()

    def shutdown(self):
        self.active_target = None
        if self._owns_controller:
            self.controller.close()

    @hook
    def on_external_browser_open_ui(self, payload):
        """Open or foreground the authenticated application UI bootstrap URL."""
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError("browser UI event requires a URL")
        self.controller.call("open_application_ui", url)
        return payload

    @hook
    def on_external_browser_open_web(self, payload):
        """Open an external HTTP(S) URL in the isolated web target."""
        url = payload.get("url") if isinstance(payload, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("browser web event requires an HTTP(S) URL")
        self.controller.call("open_web", url, new_tab=True)
        return payload

    def _require_session(self, *, web_only=False):
        target = self.active_target
        if target is None:
            raise BrowserError("Open an observation session with open_session first.")
        if web_only and target != "web":
            raise BrowserError("This operation requires open_session(target='web').")
        return target

    def _status(self, target, *, action):
        status = self.controller.session_call(target, "status")
        return {"status": "success", "action": action, "target": target, **status}

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
    async def chromium_state(self):
        """Observe without blocking worker status, input or event delivery."""
        target = self.active_target
        if target is None:
            return None
        result = await asyncio.to_thread(self._chromium_state, target)
        return result if self.active_target == target else None

    def _chromium_state(self, target):
        try:
            status = self.controller.session_call(target, "status")
            if not status.get("open") or not status.get("tabs"):
                return {"target": target, "observation_open": True, **status}
            return self._provider_observation(target)
        except Exception as error:
            return {
                "target": target,
                "error": f"Current Chromium state is unavailable: {error}",
            }

    @tool
    def open_session(self, target: str):
        """Observe web or harness_ui without creating or closing any browser tabs."""
        target = validate_target(target)
        result = self._status(target, action="open_session")
        self.active_target = target
        return result

    @tool
    def close_session(self):
        """Remove browser observation from agent context; leave all tabs/windows intact."""
        target = self.active_target
        self.active_target = None
        return {"status": "success", "action": "close_session", "target": target}

    @tool
    def status(self):
        """List tabs and browser state in the current observation session."""
        target = self._require_session()
        return {"target": target, **self.controller.session_call(target, "status")}

    @tool
    def open_web(self, url: str | None = None, new_tab: bool = False):
        """Open the web browser, optionally navigating or creating a tab."""
        target = self._require_session(web_only=True)
        self.controller.call("open_web", url, new_tab=new_tab)
        return self._status(target, action="open_web")

    @tool
    def close_tab(self, index: int | None = None):
        """Close one web tab by index, or the active web tab when omitted."""
        target = self._require_session(web_only=True)
        result = self.controller.call("close_web_tab", index)
        return {"status": "success", "action": "close_tab", "target": target, **result}

    @tool
    def close_web(self):
        """Close all web tabs and their browser window, not the application UI."""
        target = self._require_session(web_only=True)
        result = self.controller.call("close_web")
        return {"status": "success", "action": "close_web", "target": target, **result}

    @tool
    def goto(self, url: str):
        """Navigate the current web tab; arbitrary navigation is unavailable to harness_ui."""
        target = self._require_session(web_only=True)
        self.controller.call("goto", target, url)
        return self._status(target, action="goto")

    @tool
    def back(self):
        """Navigate the active tab of the current observation session backward."""
        target = self._require_session()
        self.controller.session_call(target, "back")
        return self._status(target, action="back")

    @tool
    def select_tab(self, index: int):
        """Select a zero-based tab in the current observation session."""
        target = self._require_session()
        self.controller.session_call(target, "select_tab", index)
        return self._status(target, action="select_tab")

    @tool
    def click(self, element_id: str):
        """Click an element ID from the latest observation of the current session."""
        target = self._require_session()
        self.controller.session_call(target, "click", element_id)
        return self._status(target, action="click")

    @tool
    def fill(self, element_id: str, text: str):
        """Fill an editable element ID from the latest observation of the current session."""
        target = self._require_session()
        self.controller.session_call(target, "fill", element_id, text)
        return self._status(target, action="fill")

    @tool
    def select(self, element_id: str, value: str | list[str]):
        """Select values in a select element from the current session's latest observation."""
        target = self._require_session()
        self.controller.session_call(target, "select", element_id, value)
        return self._status(target, action="select")

    @tool
    def press(self, key: str, element_id: str | None = None):
        """Press a Playwright key globally or on an observed element in the current session."""
        target = self._require_session()
        self.controller.session_call(target, "press", key, element_id)
        return self._status(target, action="press")
