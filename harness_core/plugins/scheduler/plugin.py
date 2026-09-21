from modict import modict

from ...agent.models.config import Config
from ...agent.extensions.plugin import Plugin
from ...agent.extensions.tools import tool
from harness_core.utils.ids import timestamp_id


class SchedulerConfig(Config):
    max_wakes: int = 64
    min_interval_minutes: int = 1

    @modict.validator("max_wakes", mode="after")
    def validate_max_wakes(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
            raise ValueError("scheduler.max_wakes must be within 1..256")
        return value

    @modict.validator("min_interval_minutes", mode="after")
    def validate_min_interval(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("scheduler.min_interval_minutes must be positive")
        return value


class SchedulerPlugin(Plugin):
    name = "scheduler"
    description = "Persistent one-time and periodic autonomous agent wake-ups."
    config = SchedulerConfig
    instruction_scope = "agentic"
    instructions_file = "instructions.md"

    async def _call(self, method, payload=None):
        return await self.agent.application.call(
            "scheduler",
            method,
            payload or {},
            timeout_ms=10_000,
        )

    @tool
    async def create_wake(
        self,
        name: str,
        prompt: str,
        at: str | None = None,
        interval_minutes: int | None = None,
        first_at: str | None = None,
        delivery: str = "message",
    ):
        """Create one persistent wake, either at a date or on an interval."""
        return await self._call("create", {
            "wake_id": timestamp_id(),
            "name": name,
            "prompt": prompt,
            "at": at,
            "interval_minutes": interval_minutes,
            "first_at": first_at,
            "delivery": delivery,
            "max_wakes": self.config.max_wakes,
            "min_interval_minutes": self.config.min_interval_minutes,
        })

    @tool
    async def list_wakes(self):
        """List all scheduled wakes, including disabled one-time wakes."""
        return await self._call("list")

    @tool
    async def update_wake(
        self,
        wake_id: str,
        name: str | None = None,
        prompt: str | None = None,
        delivery: str | None = None,
        enabled: bool | None = None,
    ):
        """Update a wake's label, instruction, or enabled state."""
        return await self._call("update", {
            "wake_id": wake_id,
            "name": name,
            "prompt": prompt,
            "delivery": delivery,
            "enabled": enabled,
        })

    @tool
    async def reschedule_wake(
        self,
        wake_id: str,
        at: str | None = None,
        interval_minutes: int | None = None,
        first_at: str | None = None,
    ):
        """Replace a wake's schedule with one date or a periodic interval."""
        return await self._call("reschedule", {
            "wake_id": wake_id,
            "at": at,
            "interval_minutes": interval_minutes,
            "first_at": first_at,
            "min_interval_minutes": self.config.min_interval_minutes,
        })

    @tool
    async def run_wake_now(self, wake_id: str):
        """Move a scheduled wake to now without deleting its definition."""
        return await self._call("run_now", {"wake_id": wake_id})

    @tool
    async def delete_wake(self, wake_id: str):
        """Permanently remove a scheduled wake."""
        return await self._call("delete", {"wake_id": wake_id})
