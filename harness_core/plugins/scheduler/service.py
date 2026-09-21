import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from modict import modict

from harness_core.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from harness_core.agent.models.base import Base

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
        self.error = None
        self.task = None
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.last_attempts = {}
        self.active = True

    @property
    def running(self):
        return self.task is not None and not self.task.done()

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
        return wakes

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        document = {
            "version": self.version,
            "wakes": [dict(wake) for wake in self.wakes.values()],
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
        if wake.schedule_type not in {"once", "interval"}:
            raise ValueError("schedule_type must be once or interval")
        if wake.delivery not in {"message", "alarm"}:
            raise ValueError("delivery must be message or alarm")
        parse_datetime(wake.next_run_at, field="next_run_at")
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
        if bool(at) == (interval_minutes is not None):
            raise ValueError("provide exactly one of at or interval_minutes")
        now = utc_now()
        if at:
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

    async def reschedule(
        self,
        *,
        wake_id,
        at=None,
        interval_minutes=None,
        first_at=None,
        min_interval_minutes=1,
    ):
        self._ensure_available()
        if bool(at) == (interval_minutes is not None):
            raise ValueError("provide exactly one of at or interval_minutes")
        async with self.lock:
            wake = self._wake(wake_id)
            previous = ScheduledWake(**dict(wake))
            if at:
                wake.schedule_type = "once"
                wake.next_run_at = format_datetime(parse_datetime(at))
                wake.interval_minutes = None
            else:
                if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int):
                    raise ValueError("interval_minutes must be an integer")
                if interval_minutes < min_interval_minutes:
                    raise ValueError(
                        f"interval_minutes must be at least {min_interval_minutes}"
                    )
                wake.schedule_type = "interval"
                wake.interval_minutes = interval_minutes
                wake.next_run_at = format_datetime(
                    parse_datetime(first_at, field="first_at")
                    if first_at
                    else utc_now() + timedelta(minutes=interval_minutes)
                )
            wake.enabled = True
            wake.pending_occurrence_id = None
            wake.updated_at = format_datetime(utc_now())
            self._validate_wake(wake)
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
        else:
            interval = timedelta(minutes=wake.interval_minutes)
            now = utc_now()
            while due <= now:
                due += interval
            wake.next_run_at = format_datetime(due)
        wake.updated_at = format_datetime(utc_now())

    async def _run(self):
        while True:
            await self.dispatch_due()
            self.changed.clear()
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
