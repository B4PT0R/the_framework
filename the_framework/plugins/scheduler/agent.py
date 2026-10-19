import json
from zoneinfo import ZoneInfo

from modict import modict

from ...agent.models.config import Config
from ...agent.extensions.extension import AgentExtension
from ...agent.extensions.tools import tool
from ...agent.extensions.providers import provider
from ...utils.ids import timestamp_id


def json_object(value, field):
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{field} must contain a JSON object") from error
    if not isinstance(result, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return result


class SchedulerConfig(Config):
    max_wakes: int = 64
    min_interval_minutes: int = 1
    timezone: str = "UTC"
    agenda_limit: int = 12

    @modict.validator("timezone", mode="after")
    def validate_timezone(self, value):
        ZoneInfo(value)
        return value

    @modict.validator("agenda_limit", mode="after")
    def validate_agenda_limit(self, value):
        if type(value) is not int or not 1 <= value <= 30:
            raise ValueError("agenda_limit must be within 1..30")
        return value

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


class SchedulerExtension(AgentExtension):
    name = "scheduler"
    description = "Persistent tasks, daily agenda and autonomous agent wake-ups."
    config = SchedulerConfig
    instruction_scope = "agentic"
    instructions_file = "instructions.md"

    async def _call(self, method, payload=None):
        return await self.agent.bridge.call(
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
        calendar_json: str | None = None,
    ):
        """
        description: >-
          Create a wake at a date, interval, or calendar_json.
          calendar_json encodes {time: HH:MM, timezone: IANA, weekdays?: [0..6]}.
          Calendar schedules preserve local wall time across daylight saving changes.
        """
        return await self._call("create", {
            "wake_id": timestamp_id(),
            "name": name,
            "prompt": prompt,
            "at": at,
            "interval_minutes": interval_minutes,
            "first_at": first_at,
            "delivery": delivery,
            "calendar": json_object(calendar_json, "calendar_json") if calendar_json is not None else None,
            "max_wakes": self.config.max_wakes,
            "min_interval_minutes": self.config.min_interval_minutes,
        })

    @tool
    async def list_wakes(self):
        """
        description: >-
          List all scheduled wakes, including disabled one-time wakes.
        """
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
        """
        description: >-
          Update a wake's label, instruction, or enabled state.
        """
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
        calendar_json: str | None = None,
    ):
        """
        description: >-
          Replace a wake schedule with a date, interval, or local calendar_json.
          calendar_json encodes {time: HH:MM, timezone: IANA, weekdays?: [0..6]}.
        """
        return await self._call("reschedule", {
            "wake_id": wake_id,
            "at": at,
            "interval_minutes": interval_minutes,
            "first_at": first_at,
            "calendar": json_object(calendar_json, "calendar_json") if calendar_json is not None else None,
            "min_interval_minutes": self.config.min_interval_minutes,
        })

    @tool
    async def run_wake_now(self, wake_id: str):
        """
        description: >-
          Move a scheduled wake to now without deleting its definition.
        """
        return await self._call("run_now", {"wake_id": wake_id})

    @tool
    async def delete_wake(self, wake_id: str):
        """
        description: >-
          Permanently remove a scheduled wake.
        """
        return await self._call("delete", {"wake_id": wake_id})

    @provider
    async def daily_tasks(self):
        """
        description: >-
          Live bounded agenda: overdue tasks, today's actions and pending reminders.
        """
        try:
            return await self._call("task_agenda", {"timezone": self.config.timezone, "limit": self.config.agenda_limit})
        except Exception as error:
            return {"status": "unavailable", "error": str(error), "warning": "Task state is unavailable, not empty."}

    @tool
    async def create_task(self, task_json: str):
        """
        description: >-
          Create a tracked task from a JSON object string. Fields: title, tags, priority(0..3), next_action, notes,
          timezone, due_at(date or offset datetime), planned_at, reminder_at,
          remind_before_minutes(list), overdue_after_minutes, recurrence(calendar).
          No deadline/reminder is inferred. Tasks require explicit completion.
        """
        task = {"timezone": self.config.timezone, **json_object(task_json, "task_json")}
        return await self._call("task_create", {"task_id": timestamp_id(), "task": task})

    @tool
    async def get_task(self, task_id: str):
        """
        description: >-
          Read a task's full state, revision, occurrence and recent history.
        """
        return await self._call("task_get", {"task_id": task_id})

    @tool
    async def find_tasks(self, query_json: str = "{}"):
        """
        description: >-
          Filter tasks with a JSON object string: status(active/done/cancelled/all), tags, text, from_date/to_date
          (inclusive local dates), timezone, overdue, undated, limit(1..100), offset.
          Defaults to active tasks. Returns compact rows; get_task gives full details.
        """
        return await self._call("task_find", {"query": {"timezone": self.config.timezone, **json_object(query_json, "query_json")}})

    @tool
    async def update_task(self, task_id: str, changes_json: str, expected_revision: int):
        """
        description: >-
          Edit task fields using a JSON object string. Omitted fields stay unchanged;
          null clears a nullable field. Read the current revision first.
          Do not change a real deadline merely to defer a reminder.
        """
        return await self._call("task_update", {"task_id": task_id, "changes": json_object(changes_json, "changes_json"), "expected_revision": expected_revision})

    @tool
    async def resolve_task(self, task_id: str, action: str, expected_revision: int,
                           at: str | None = None, scope: str = "occurrence"):
        """
        description: >-
          Explicitly done/cancel/skip/postpone/snooze a task. Require user confirmation.
          postpone changes the planned date and any existing deadline; snooze only the reminder.
          Both require a future at (offset datetime). For recurrence, occurrence affects
          this instance; scope=series closes the series with done/cancel. skip advances it.
        """
        return await self._call("task_resolve", {"task_id": task_id, "action": action,
            "expected_revision": expected_revision, "at": at, "scope": scope})
