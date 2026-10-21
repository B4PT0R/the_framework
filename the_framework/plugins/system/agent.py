from datetime import datetime

from ...agent.extensions.extension import AgentExtension
from ...agent.extensions.providers import provider
from ...agent.extensions.tools import tool
from the_framework.utils.ids import timestamp_id


class SystemExtension(AgentExtension):
    name = "system"
    description = "Controlled interface refresh, runtime inspection and supervised server restart."
    instruction_scope = "agentic"
    instructions_file = "instructions.md"

    @staticmethod
    def _now():
        return datetime.now().astimezone()

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
    async def surfaces(self):
        """Inspect published, previous and preview releases, including running builds."""
        return await self.agent.bridge.call("surfaces", "list", {}, timeout_ms=10_000)

    @tool
    async def build_surface(self, name: str = "main"):
        """Build an isolated immutable candidate for one client surface."""
        result = await self.agent.bridge.call(
            "surfaces", "build", {"name": name}, timeout_ms=600_000,
        )
        return result

    @tool
    async def preview_surface(self, name: str = "main", release: str | None = None):
        """Open an authenticated read-only preview without replacing the desktop."""
        return await self.agent.bridge.call(
            "surfaces", "preview", {"name": name, "release": release}, timeout_ms=60_000,
        )

    @tool
    async def publish_surface(self, name: str = "main", release: str | None = None):
        """Publish a verified candidate and refresh connected clients."""
        return await self.agent.bridge.call(
            "surfaces",
            "publish",
            {"name": name, "release": release},
            timeout_ms=60_000,
        )

    @tool
    async def rollback_surface(self, name: str = "main"):
        """Restore the preceding client-surface release without rebuilding."""
        return await self.agent.bridge.call(
            "surfaces", "rollback", {"name": name}, timeout_ms=60_000,
        )

    @tool
    async def reboot_server(self, resume_instruction: str | None = None):
        """Restart the supervised server after this turn, then resume autonomously."""
        action_id = timestamp_id()
        result = await self.agent.bridge.call(
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
        return await self.agent.bridge.call(
            "plugins", "list", {}, timeout_ms=10_000
        )

    @tool
    async def set_plugin_binding(self, name: str, enabled: bool):
        """Enable or disable one plugin's tools and context for this agent."""
        return await self.agent.bridge.call(
            "plugins",
            "set_binding",
            {"name": name, "enabled": enabled},
            timeout_ms=10_000,
        )
