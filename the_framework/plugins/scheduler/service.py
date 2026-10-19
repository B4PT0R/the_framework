import asyncio
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from modict import modict

from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from the_framework.agent.models.base import Base

from .calendar import calendar_schedule, next_calendar
from .tasks import TaskBook

UTC = timezone.utc


def utc_now():
    return datetime.now(UTC)


def format_datetime(value):
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def parse_datetime(value, *, field="at"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO 8601 date and time")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO 8601 date and time") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(UTC)


class ScheduledWake(Base):
    id: str = modict.field(required="always")
    name: str = modict.field(required="always")
    prompt: str = modict.field(required="always")
    schedule_type: str = modict.field(required="always")
    next_run_at: str = modict.field(required="always")
    interval_minutes: int | None = None
    calendar: dict | None = None
    delivery: str = "message"
    enabled: bool = True
    created_at: str = modict.field(required="always")
    updated_at: str = modict.field(required="always")
    last_run_at: str | None = None
    last_error: str | None = None
    pending_occurrence_id: str | None = None
    delivery_attempt: int = 0


class SchedulerService:
    """Persistent clock owned by FastAPI and feeding the canonical worker queue."""

    version = 1

    def __init__(self, path, deliver, *, poll_interval=1.0, retry_interval=5.0):
        self.path = Path(path)
        self.deliver = deliver
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval
        self.wakes = {}
        self.tasks = TaskBook()
        self.error = None
        self.task = None
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.last_attempts = {}
        self.active = True

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def health(self):
        return {
            "running": self.running, "active": self.active,
            "error": self.error, "wakes": len(self.wakes), "tasks": len(self.tasks.tasks),
        }

    async def start(self, *, active=True):
        if self.running:
            return
        self.active = bool(active)
        self.error = None
        try:
            loaded = await asyncio.to_thread(self._load)
        except Exception as error:
            self.error = f"could not load scheduler state: {error}"
            loaded = {}
        self.wakes = loaded
        self.task = asyncio.create_task(self._run(), name="agent-scheduler")

    async def stop(self):
        if self.task is None:
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.task = None

    def _load(self):
        if not self.path.exists():
            self.tasks = TaskBook()
            return {}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("version") != self.version:
            raise ValueError("unsupported scheduler document")
        records = document.get("wakes")
        if not isinstance(records, list):
            raise ValueError("scheduler document must contain a wakes list")
        wakes = {}
        for record in records:
            wake = ScheduledWake(**record)
            self._validate_wake(wake)
            if wake.id in wakes:
                raise ValueError(f"duplicate scheduled wake: {wake.id}")
            wakes[wake.id] = wake
        self.tasks = TaskBook(document.get("tasks", []))
        return wakes

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        document = {
            "version": self.version,
            "wakes": [dict(wake) for wake in self.wakes.values()],
            "tasks": self.tasks.snapshot(),
        }
        with temporary.open("w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(document, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _ensure_available(self):
        if self.error is not None:
            raise RuntimeError(self.error)

    @staticmethod
    def _validate_wake(wake):
        if wake.schedule_type not in {"once", "interval", "calendar"}:
            raise ValueError("schedule_type must be once, interval or calendar")
        if wake.delivery not in {"message", "alarm"}:
            raise ValueError("delivery must be message or alarm")
        parse_datetime(wake.next_run_at, field="next_run_at")
        if wake.schedule_type == "calendar":
            calendar_schedule(wake.calendar)
            if wake.interval_minutes is not None:
                raise ValueError("calendar wakes cannot define interval_minutes")
        elif wake.calendar is not None:
            raise ValueError("calendar is only valid for calendar wakes")
        if wake.schedule_type == "once" and wake.interval_minutes is not None:
            raise ValueError("one-time wakes cannot define interval_minutes")
        if wake.schedule_type == "interval":
            if (
                isinstance(wake.interval_minutes, bool)
                or not isinstance(wake.interval_minutes, int)
                or wake.interval_minutes <= 0
            ):
                raise ValueError("interval wakes require positive interval_minutes")

    async def handle(self, method, payload):
        handlers = {
            "create": self.create,
            "list": self.list,
            "update": self.update,
            "reschedule": self.reschedule,
            "delete": self.delete,
            "run_now": self.run_now,
        }
        if method.startswith("task_"):
            return await self.task_call(method[5:], payload)
        handler = handlers.get(method)
        if handler is None:
            raise ValueError(f"unsupported scheduler method: {method}")
        return await handler(**payload)

    async def create(
        self,
        *,
        wake_id,
        name,
        prompt,
        at=None,
        interval_minutes=None,
        first_at=None,
        calendar=None,
        delivery="message",
        max_wakes=64,
        min_interval_minutes=1,
    ):
        self._ensure_available()
        if isinstance(max_wakes, bool) or not isinstance(max_wakes, int) or not 1 <= max_wakes <= 256:
            raise ValueError("max_wakes must be within 1..256")
        if (
            isinstance(min_interval_minutes, bool)
            or not isinstance(min_interval_minutes, int)
            or min_interval_minutes <= 0
        ):
            raise ValueError("min_interval_minutes must be positive")
        name = self._required_text(name, "name", 160)
        prompt = self._required_text(prompt, "prompt", 16_000)
        if delivery not in {"message", "alarm"}:
            raise ValueError("delivery must be message or alarm")
        if sum((bool(at), interval_minutes is not None, calendar is not None)) != 1:
            raise ValueError("provide exactly one of at, interval_minutes or calendar")
        if first_at is not None and interval_minutes is None:
            raise ValueError("first_at is only valid for interval wakes")
        now = utc_now()
        if calendar is not None:
            calendar = calendar_schedule(calendar)
            schedule_type = "calendar"
            next_run = next_calendar(calendar, now)
            interval = None
        elif at:
            schedule_type = "once"
            next_run = parse_datetime(at)
            interval = None
        else:
            if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int):
                raise ValueError("interval_minutes must be an integer")
            if interval_minutes < min_interval_minutes:
                raise ValueError(
                    f"interval_minutes must be at least {min_interval_minutes}"
                )
            schedule_type = "interval"
            interval = interval_minutes
            next_run = (
                parse_datetime(first_at, field="first_at")
                if first_at
                else now + timedelta(minutes=interval)
            )
        timestamp = format_datetime(now)
        wake = ScheduledWake(
            id=self._required_text(wake_id, "wake_id", 200),
            name=name,
            prompt=prompt,
            schedule_type=schedule_type,
            next_run_at=format_datetime(next_run),
            interval_minutes=interval,
            calendar=calendar,
            delivery=delivery,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._validate_wake(wake)
        async with self.lock:
            if wake.id in self.wakes:
                raise ValueError(f"scheduled wake already exists: {wake.id}")
            if len(self.wakes) >= max_wakes:
                raise ValueError(f"scheduler already contains {len(self.wakes)} wakes")
            self.wakes[wake.id] = wake
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.wakes.pop(wake.id, None)
                raise
        self.changed.set()
        return dict(wake)

    async def list(self):
        self._ensure_available()
        async with self.lock:
            wakes = sorted(self.wakes.values(), key=lambda wake: wake.next_run_at)
            return {"wakes": [dict(wake) for wake in wakes]}

    async def update(self, *, wake_id, name=None, prompt=None, delivery=None, enabled=None):
        self._ensure_available()
        async with self.lock:
            wake = self._wake(wake_id)
            previous = ScheduledWake(**dict(wake))
            if name is not None:
                wake.name = self._required_text(name, "name", 160)
            if prompt is not None:
                wake.prompt = self._required_text(prompt, "prompt", 16_000)
            if delivery is not None:
                if delivery not in {"message", "alarm"}:
                    raise ValueError("delivery must be message or alarm")
                wake.delivery = delivery
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise ValueError("enabled must be a boolean")
                wake.enabled = enabled
            wake.updated_at = format_datetime(utc_now())
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.wakes[wake.id] = previous
                raise
        self.changed.set()
        return dict(wake)

    async def reschedule(self, *, wake_id, at=None, interval_minutes=None, first_at=None,
                         calendar=None, min_interval_minutes=1):
        self._ensure_available()
        if sum((bool(at), interval_minutes is not None, calendar is not None)) != 1:
            raise ValueError("provide exactly one of at, interval_minutes or calendar")
        if first_at is not None and interval_minutes is None:
            raise ValueError("first_at is only valid for interval wakes")
        if calendar is not None:
            calendar = calendar_schedule(calendar)
            kind, due = "calendar", next_calendar(calendar, utc_now())
        elif at:
            kind, due = "once", parse_datetime(at)
        else:
            if type(min_interval_minutes) is not int or min_interval_minutes < 1:
                raise ValueError("min_interval_minutes must be positive")
            if type(interval_minutes) is not int or interval_minutes < min_interval_minutes:
                raise ValueError("interval_minutes is below the minimum")
            kind = "interval"
            due = parse_datetime(first_at, field="first_at") if first_at else utc_now() + timedelta(minutes=interval_minutes)
        async with self.lock:
            wake = self._wake(wake_id)
            previous = deepcopy(wake)
            wake.update(schedule_type=kind, next_run_at=format_datetime(due),
                        interval_minutes=interval_minutes, calendar=calendar, enabled=True,
                        pending_occurrence_id=None, delivery_attempt=0, updated_at=format_datetime(utc_now()))
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.wakes[wake.id] = previous
                raise
        self.changed.set()
        return dict(wake)

    async def delete(self, *, wake_id):
        self._ensure_available()
        async with self.lock:
            wake = self._wake(wake_id)
            self.wakes.pop(wake.id)
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.wakes[wake.id] = wake
                raise
        self.last_attempts.pop(wake.pending_occurrence_id, None)
        self.changed.set()
        return {"id": wake.id, "status": "deleted"}

    async def run_now(self, *, wake_id):
        self._ensure_available()
        async with self.lock:
            wake = self._wake(wake_id)
            previous = ScheduledWake(**dict(wake))
            wake.enabled = True
            wake.next_run_at = format_datetime(utc_now())
            wake.pending_occurrence_id = None
            wake.updated_at = wake.next_run_at
            try:
                await asyncio.to_thread(self._save)
            except Exception:
                self.wakes[wake.id] = previous
                raise
        self.changed.set()
        return dict(wake)

    async def observe(self, output):
        if not isinstance(output, (CommandCompleted, CommandFailed)):
            return
        command_id = output.command.id
        try:
            async with self.lock:
                error = output.error if isinstance(output, CommandFailed) else None
                if self.tasks.observe(command_id, error):
                    await asyncio.to_thread(self._save)
                    self.last_attempts.pop(command_id, None)
                    self.changed.set()
                    return
                wake = next(
                    (
                        candidate
                        for candidate in self.wakes.values()
                        if candidate.pending_occurrence_id == command_id
                    ),
                    None,
                )
                if wake is None:
                    return
                if isinstance(output, CommandFailed) and output.error == "interrupted":
                    wake.pending_occurrence_id = None
                    wake.delivery_attempt += 1
                    wake.last_error = output.error
                    wake.updated_at = format_datetime(utc_now())
                else:
                    wake.last_error = output.error if isinstance(output, CommandFailed) else None
                    self._advance(wake)
                await asyncio.to_thread(self._save)
        except Exception as error:
            self.error = f"could not persist scheduler delivery: {error}"
            return
        self.last_attempts.pop(command_id, None)
        self.changed.set()

    async def dispatch_due(self, now=None):
        if self.error is not None or not self.active:
            return
        now = now or utc_now()
        deliveries = []
        async with self.lock:
            for wake in self.wakes.values():
                if not wake.enabled or parse_datetime(wake.next_run_at) > now:
                    continue
                occurrence_id = wake.pending_occurrence_id
                if occurrence_id is None:
                    due_key = wake.next_run_at.replace(":", "").replace("+", "p")
                    occurrence_id = (
                        f"scheduled-wake-{wake.id}-{due_key}-{wake.delivery_attempt}"
                    )
                    wake.pending_occurrence_id = occurrence_id
                    wake.updated_at = format_datetime(now)
                    await asyncio.to_thread(self._save)
                last_attempt = self.last_attempts.get(occurrence_id)
                if last_attempt is not None and (now - last_attempt).total_seconds() < self.retry_interval:
                    continue
                self.last_attempts[occurrence_id] = now
                deliveries.append((wake, occurrence_id))
        await self.dispatch_tasks(now)
        for wake, occurrence_id in deliveries:
            body = (
                f'<scheduled_wake id="{escape(wake.id)}" '
                f'name="{escape(wake.name)}" scheduled_for="{escape(wake.next_run_at)}" '
                f'delivery="{escape(wake.delivery)}">'
                f'<instruction>{escape(wake.prompt)}</instruction>'
                "</scheduled_wake>"
            )
            try:
                await self.deliver(PromptRequest(
                    id=occurrence_id,
                    prompt=body,
                    prompt_role="developer",
                    prompt_kind=(
                        "scheduled_alarm" if wake.delivery == "alarm" else "scheduled_wake"
                    ),
                ))
            except Exception:
                # The stable command ID makes a later retry idempotent if the first
                # write reached the worker before the transport failed.
                continue

    def _advance(self, wake):
        due = parse_datetime(wake.next_run_at)
        wake.last_run_at = format_datetime(utc_now())
        wake.pending_occurrence_id = None
        wake.delivery_attempt = 0
        if wake.schedule_type == "once":
            wake.enabled = False
        elif wake.schedule_type == "calendar":
            wake.next_run_at = format_datetime(next_calendar(wake.calendar, utc_now()))
        else:
            interval = timedelta(minutes=wake.interval_minutes)
            now = utc_now()
            while due <= now:
                due += interval
            wake.next_run_at = format_datetime(due)
        wake.updated_at = format_datetime(utc_now())

    async def task_call(self, method, payload):
        self._ensure_available()
        now = utc_now()
        async with self.lock:
            if method == "get":
                return self.tasks.view(self.tasks.get(payload["task_id"]), now)
            if method == "find":
                return self.tasks.find(payload.get("query", {}), now)
            if method == "agenda":
                return self.tasks.agenda(now, payload.get("timezone", "UTC"), payload.get("limit", 12))
            previous = self.tasks.snapshot()
            try:
                if method == "create":
                    if len(self.tasks.tasks) >= 2000:
                        raise ValueError("task limit reached (2000)")
                    result = self.tasks.create(payload["task_id"], payload["task"], now)
                elif method == "update":
                    result = self.tasks.update(payload["task_id"], payload["changes"], payload["expected_revision"], now)
                elif method == "resolve":
                    result = self.tasks.resolve(payload["task_id"], payload["action"], payload["expected_revision"], now,
                                                at=payload.get("at"), scope=payload.get("scope", "occurrence"))
                else:
                    raise ValueError(f"unsupported task method: {method}")
                await asyncio.to_thread(self._save)
            except Exception:
                self.tasks = TaskBook(previous)
                raise
        self.changed.set()
        return result

    async def dispatch_tasks(self, now):
        async with self.lock:
            previous = self.tasks.snapshot()
            prepared = self.tasks.prepare(now)
            if previous != self.tasks.snapshot():
                try:
                    await asyncio.to_thread(self._save)
                except Exception:
                    self.tasks = TaskBook(previous)
                    raise
            deliveries = []
            for task, occurrence_id in prepared:
                last = self.last_attempts.get(occurrence_id)
                if last is not None and (now - last).total_seconds() < self.retry_interval:
                    continue
                self.last_attempts[occurrence_id] = now
                deliveries.append((task.id, task.revision, occurrence_id))
        for task_id, revision, occurrence_id in deliveries:
            async with self.lock:
                task = self.tasks.get(task_id)
                if task.status != "active" or task.revision != revision or not task.pending or task.pending["id"] != occurrence_id:
                    continue
                body = (f'<scheduled_task id="{escape(task.id)}" revision="{revision}" '
                        f'occurrence_id="{escape(task.occurrence_id)}">'
                        f'<title>{escape(task.title)}</title>'
                        'A task reminder is due. Read scheduler.get_task before acting; if its revision '
                        'or occurrence changed, or it is closed, this reminder is obsolete. '
                        'Use the current task data and conversation to give a brief helpful reminder. '
                        'Delivery is not completion: only explicit user confirmation resolves a task. '
                        'Never open a microphone or escalate to an alarm automatically.'
                        '</scheduled_task>')
            try:
                await self.deliver(PromptRequest(id=occurrence_id, prompt=body, prompt_role="developer", prompt_kind="scheduled_wake"))
            except Exception:
                continue

    async def _run(self):
        while True:
            self.changed.clear()
            try:
                await self.dispatch_due()
            except Exception as error:
                self.error = f"could not dispatch scheduler reminders: {error}"
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=self.poll_interval)
            except TimeoutError:
                pass

    def _wake(self, wake_id):
        wake = self.wakes.get(wake_id)
        if wake is None:
            raise ValueError(f"unknown scheduled wake: {wake_id}")
        return wake

    @staticmethod
    def _required_text(value, field, maximum):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be empty")
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f"{field} exceeds {maximum} characters")
        return value
