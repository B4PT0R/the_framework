import time
from datetime import datetime

from ...agent.models.config import Config
from ...agent.extensions.plugin import Plugin
from ...agent.extensions.providers import provider
from ...agent.extensions.tools import tool
from the_framework.utils.ids import timestamp_id


class SystemConfig(Config):
    default_wait_seconds: int = 30
    max_wait_seconds: int = 3600


class SystemPlugin(Plugin):
    name = "system"
    description = "Graceful turn completion, controlled interface refresh, and supervised server restart."
    instruction_scope = "agentic"
    instructions_file = "instructions.md"
    config = SystemConfig

    @staticmethod
    def _now():
        return datetime.now().astimezone()

    @provider
    def turn_timing(self):
        """Current turn boundary and timing of the previous completed turn."""
        timing = getattr(self.agent, "turn_timing", None)
        return timing.context() if timing is not None else None

    @provider
    def current_datetime(self):
        """Current local date, time, weekday, timezone, and UTC offset."""
        now = self._now()
        return {
            "date": now.date().isoformat(),
            "time": now.timetz().isoformat(timespec="seconds"),
            "weekday": now.strftime("%A"),
            "timezone": now.tzname(),
            "utc_offset": now.strftime("%z")[:3] + ":" + now.strftime("%z")[3:],
        }

    @tool
    def end_turn(self):
        """End the current turn gracefully after the current tool-call batch."""
        self.agent.end_turn()
        return {"status": "end_of_turn_requested"}

    @tool
    def wait(self, timeout_seconds: int | None = None):
        """
        description: Wait idly until the timeout expires, the user steers the active turn, or the turn is interrupted. Worker IPC remains responsive throughout.
        parameters:
          properties:
            timeout_seconds:
              description: Optional wait duration in seconds, capped by plugin configuration. Null uses the configured default.
        """
        duration = (
            self.config.default_wait_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 0 <= duration <= self.config.max_wait_seconds
        ):
            raise ValueError(
                f"wait timeout_seconds must be within 0..{self.config.max_wait_seconds}"
            )
        started_at = time.monotonic()
        reason = self.agent.agentic_loop.wait_for_activity(duration)
        return {
            "status": "elapsed" if reason == "timeout" else reason,
            "waited_seconds": round(time.monotonic() - started_at, 3),
        }

    @tool
    async def build_surface(self, name: str = "main"):
        """Build an isolated immutable candidate for one client surface."""
        result = await self.agent.application.call(
            "surfaces", "build", {"name": name}, timeout_ms=600_000,
        )
        return result

    @tool
    async def publish_surface(self, name: str = "main", release: str | None = None):
        """Publish a verified candidate and refresh connected clients."""
        return await self.agent.application.call(
            "surfaces",
            "publish",
            {"name": name, "release": release},
            timeout_ms=60_000,
        )

    @tool
    async def rollback_surface(self, name: str = "main"):
        """Restore the preceding client-surface release without rebuilding."""
        return await self.agent.application.call(
            "surfaces", "rollback", {"name": name}, timeout_ms=60_000,
        )

    @tool
    async def reboot_server(self, resume_instruction: str | None = None):
        """Restart the supervised server after this turn, then resume autonomously."""
        action_id = timestamp_id()
        result = await self.agent.application.call(
            "system",
            "reboot_server",
            {
                "action_id": action_id,
                "resume_instruction": resume_instruction,
            },
            timeout_ms=10_000,
        )
        self.agent.end_turn()
        return result

    @tool
    async def plugins(self):
        """List installed plugin runtime and agent-binding states."""
        return await self.agent.application.call(
            "plugins", "list", {}, timeout_ms=10_000
        )

    @tool
    async def set_plugin_binding(self, name: str, enabled: bool):
        """Enable or disable one plugin's tools and context for this agent."""
        return await self.agent.application.call(
            "plugins",
            "set_binding",
            {"name": name, "enabled": enabled},
            timeout_ms=10_000,
        )

    @tool
    async def set_plugin_runtime(self, name: str, running: bool):
        """Start or fully stop one already installed plugin runtime."""
        return await self.agent.application.call(
            "plugins",
            "set_runtime",
            {"name": name, "running": running},
            timeout_ms=10_000,
        )
