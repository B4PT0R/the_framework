"""Task state and derived reminders; persistence/clock belong to SchedulerService."""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from modict import modict

from ...agent.models.base import Base
from ...utils.ids import timestamp_id
from .calendar import calendar_schedule, next_calendar

UTC = timezone.utc


def instant(value):
    if not isinstance(value, str):
        raise ValueError("expected an ISO date/time with UTC offset")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("date/time must include a UTC offset")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="milliseconds")


def deadline(task):
    value = task.due_at
    if value is None:
        return None
    if len(value) == 10:
        # A date-only deadline is not overdue until the following local day.
        return datetime.combine(date.fromisoformat(value) + timedelta(days=1), time(), ZoneInfo(task.timezone)).astimezone(UTC)
    return instant(value)


class TrackedTask(Base):
    _config = Base.config(strict=True, extra="forbid")

    id: str = modict.field(required="always")
    title: str = modict.field(required="always")
    timezone: str = "UTC"
    tags: list[str] = modict.factory(list)
    priority: int = 0
    next_action: str = ""
    notes: str = ""
    due_at: str | None = None
    planned_at: str | None = None
    recurrence: dict | None = None
    reminder_at: str | None = None
    remind_before_minutes: list[int] = modict.factory(list)
    overdue_after_minutes: int | None = None
    status: str = "active"
    revision: int = 1
    occurrence_id: str = modict.field(required="always")
    created_at: str = modict.field(required="always")
    updated_at: str = modict.field(required="always")
    history: list[dict] = modict.factory(list)
    notified: list[str] = modict.factory(list)
    pending: dict | None = None
    delivery_attempt: int = 0
    delivery_failures: int = 0
    last_error: str | None = None


EDITABLE = {"title", "timezone", "tags", "priority", "next_action", "notes", "due_at", "planned_at", "recurrence", "reminder_at", "remind_before_minutes", "overdue_after_minutes"}
TIMING = {"timezone", "due_at", "planned_at", "recurrence", "reminder_at", "remind_before_minutes", "overdue_after_minutes"}


def validate(task):
    if not task.id or len(task.id) > 200 or not task.occurrence_id:
        raise ValueError("invalid task or occurrence id")
    if not isinstance(task.title, str) or not task.title.strip() or len(task.title) > 240:
        raise ValueError("title must contain 1..240 characters")
    task.title = task.title.strip()
    ZoneInfo(task.timezone)
    if not isinstance(task.tags, list) or len(task.tags) > 16 or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 64 for tag in task.tags):
        raise ValueError("tags must contain at most 16 nonempty strings of at most 64 characters")
    task.tags = sorted(set(tag.strip().casefold() for tag in task.tags))
    if type(task.priority) is not int or not 0 <= task.priority <= 3:
        raise ValueError("priority must be 0..3 (3 is highest)")
    for field in ("notes", "next_action"):
        if not isinstance(task[field], str) or len(task[field]) > (4000 if field == "notes" else 500):
            raise ValueError(f"invalid {field}")
    for field in ("planned_at", "reminder_at"):
        if task[field] is not None:
            task[field] = iso(instant(task[field]))
    if task.due_at is not None:
        deadline(task)
        if len(task.due_at) != 10:
            task.due_at = iso(instant(task.due_at))
    if task.recurrence is not None:
        task.recurrence = calendar_schedule(task.recurrence)
        if task.recurrence["timezone"] != task.timezone:
            raise ValueError("task timezone and recurrence timezone must match")
        if task.due_at is None or len(task.due_at) == 10:
            raise ValueError("recurring tasks require a timed occurrence")
    if not isinstance(task.remind_before_minutes, list) or len(task.remind_before_minutes) > 16 or any(type(n) is not int or not 0 <= n <= 525600 for n in task.remind_before_minutes):
        raise ValueError("remind_before_minutes must contain at most 16 offsets within 0..525600")
    task.remind_before_minutes = sorted(set(task.remind_before_minutes), reverse=True)
    if task.overdue_after_minutes is not None and (type(task.overdue_after_minutes) is not int or not 0 <= task.overdue_after_minutes <= 525600):
        raise ValueError("overdue_after_minutes must be null or within 0..525600")
    if task.status not in {"active", "done", "cancelled"}:
        raise ValueError("invalid task status")
    if type(task.revision) is not int or task.revision < 1:
        raise ValueError("invalid task revision")
    for field in ("created_at", "updated_at"):
        instant(task[field])
    if task.remind_before_minutes and deadline(task) is None and task.planned_at is None:
        raise ValueError("advance reminders require due_at or planned_at")
    if task.overdue_after_minutes is not None and task.due_at is None:
        raise ValueError("overdue alert requires due_at")
    if type(task.delivery_attempt) is not int or task.delivery_attempt < 0:
        raise ValueError("invalid delivery attempt")
    if type(task.delivery_failures) is not int or not 0 <= task.delivery_failures < 3:
        raise ValueError("invalid delivery failure count")
    if task.pending is not None:
        expected_id = f"task-{task.id}-{task.revision}-{task.delivery_attempt}"
        keys = task.pending.get("keys")
        if (set(task.pending) != {"id", "keys"} or task.pending["id"] != expected_id
                or not isinstance(keys, list) or not keys
                or any(key not in dict(TaskBook.events(task)) for key in keys)):
            raise ValueError("invalid pending task reminder")
    return task


def task_days(row, zone):
    values = (row["due_at"], row["planned_at"], row["next_reminder_at"])
    return [date.fromisoformat(value) if len(value) == 10 else instant(value).astimezone(zone).date()
            for value in values if value]


def task_order(row):
    return (not row["overdue"], -row["priority"], row["due_at"] or row["planned_at"] or "9999", row["id"])


class TaskBook:
    def __init__(self, records=()):
        self.tasks = {}
        for record in records:
            task = validate(TrackedTask(**record))
            if task.id in self.tasks:
                raise ValueError(f"duplicate task: {task.id}")
            self.tasks[task.id] = task

    def snapshot(self):
        return deepcopy([dict(task) for task in self.tasks.values()])

    def get(self, task_id):
        try:
            return self.tasks[task_id]
        except KeyError:
            raise ValueError(f"unknown task: {task_id}") from None

    def create(self, task_id, payload, now):
        if not isinstance(payload, dict) or set(payload) - EDITABLE:
            raise ValueError("unknown task fields")
        if task_id in self.tasks:
            raise ValueError(f"task already exists: {task_id}")
        task = TrackedTask(**payload, id=task_id, occurrence_id=timestamp_id(), created_at=iso(now), updated_at=iso(now))
        if task.recurrence and task.due_at is None:
            task.due_at = iso(next_calendar(task.recurrence, now))
        validate(task)
        self.tasks[task.id] = task
        return self.view(task, now)

    @staticmethod
    def check_revision(task, expected_revision):
        if type(expected_revision) is not int or task.revision != expected_revision:
            raise ValueError(f"task changed; expected revision {task.revision}, read it again")

    @staticmethod
    def record(task, action, now):
        task.history = (task.history + [{"action": action, "at": iso(now), "occurrence_id": task.occurrence_id, "due_at": task.due_at, "planned_at": task.planned_at}])[-32:]
        task.revision += 1
        task.updated_at = iso(now)
        task.pending = None
        task.delivery_attempt = 0
        task.delivery_failures = 0

    def update(self, task_id, changes, expected_revision, now):
        task = self.get(task_id)
        self.check_revision(task, expected_revision)
        if task.status != "active":
            raise ValueError("only active tasks may be edited")
        if not isinstance(changes, dict) or not changes or set(changes) - EDITABLE:
            raise ValueError("unknown or empty task changes")
        self.record(task, "updated", now)
        task.update(deepcopy(changes))
        if TIMING.intersection(changes):
            task.notified = []
            task.last_error = None
        if task.recurrence and "recurrence" in changes and "due_at" not in changes:
            task.due_at = iso(next_calendar(task.recurrence, now))
        validate(task)
        return self.view(task, now)

    def resolve(self, task_id, action, expected_revision, now, *, at=None, scope="occurrence"):
        task = self.get(task_id)
        self.check_revision(task, expected_revision)
        if action not in {"done", "cancel", "skip", "postpone", "snooze"} or scope not in {"occurrence", "series"}:
            raise ValueError("invalid resolution action or scope")
        if task.status != "active":
            raise ValueError("task is already closed")
        if action in {"postpone", "snooze"}:
            if scope != "occurrence" or at is None or instant(at) <= now:
                raise ValueError("postpone/snooze require a future at and occurrence scope")
        elif at is not None:
            raise ValueError("at is only valid for postpone/snooze")
        if action == "skip" and (not task.recurrence or scope != "occurrence"):
            raise ValueError("skip requires a recurring task and occurrence scope")
        if scope == "series" and (not task.recurrence or action not in {"done", "cancel"}):
            raise ValueError("series scope requires a recurring task and done/cancel")
        self.record(task, action, now)
        task.last_error = None
        if action == "snooze":
            # A reminder deferment must not silently move the actual deadline.
            task.notified = sorted(set(task.notified + [key for key, when in self.events(task) if when <= instant(at)]))
            task.reminder_at = iso(instant(at))
            task.notified = [key for key in task.notified if key != "reminder:" + task.reminder_at]
        elif action == "postpone":
            task.due_at = iso(instant(at)) if task.due_at is not None else None
            task.planned_at = iso(instant(at))
            task.reminder_at = iso(instant(at)) if task.reminder_at else None
            task.notified = []
        elif task.recurrence and scope == "occurrence":
            task.due_at = iso(next_calendar(task.recurrence, max(now, deadline(task))))
            task.planned_at = None
            task.reminder_at = None
            task.notified = []
            task.occurrence_id = timestamp_id()
        else:
            task.status = "done" if action == "done" else "cancelled"
        validate(task)
        return self.view(task, now)

    @staticmethod
    def events(task):
        events = []
        if task.reminder_at:
            events.append(("reminder:" + task.reminder_at, instant(task.reminder_at)))
        anchor = instant(task.planned_at) if task.planned_at else deadline(task)
        if anchor:
            events.extend((f"before:{iso(anchor)}:{offset}", anchor - timedelta(minutes=offset)) for offset in task.remind_before_minutes)
        if task.overdue_after_minutes is not None:
            due = deadline(task)
            events.append(("overdue:" + iso(due), due + timedelta(minutes=task.overdue_after_minutes)))
        return events

    def view(self, task, now, *, compact=False):
        due = deadline(task)
        pending = [when for key, when in self.events(task) if key not in task.notified] if task.status == "active" else []
        result = deepcopy(dict(task))
        result.update(overdue=task.status == "active" and due is not None and now >= due,
                      next_reminder_at=iso(min(pending)) if pending else None)
        if compact:
            return {key: result[key] for key in ("id", "title", "revision", "occurrence_id", "tags", "priority", "status", "next_action", "due_at", "planned_at", "timezone", "overdue", "next_reminder_at", "last_error")}
        return result

    def find(self, query, now):
        allowed = {"status", "tags", "text", "from_date", "to_date", "timezone", "overdue", "undated", "limit", "offset"}
        if not isinstance(query, dict) or set(query) - allowed:
            raise ValueError("unknown task query fields")
        zone = ZoneInfo(query.get("timezone", "UTC"))
        status = query.get("status", "active")
        if status not in {"active", "done", "cancelled", "all"}:
            raise ValueError("invalid status filter")
        start = date.fromisoformat(query["from_date"]) if query.get("from_date") else None
        end = date.fromisoformat(query["to_date"]) if query.get("to_date") else None
        if start and end and start > end:
            raise ValueError("from_date must be before to_date")
        limit, offset = query.get("limit", 30), query.get("offset", 0)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1..100 and offset nonnegative")
        tags = query.get("tags", [])
        text = query.get("text", "")
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise ValueError("tags filter must be a list of nonempty strings")
        tags = {tag.strip().casefold() for tag in tags}
        if not isinstance(text, str):
            raise ValueError("text filter must be a string")
        for field in ("overdue", "undated"):
            if field in query and type(query[field]) is not bool:
                raise ValueError(f"{field} filter must be boolean")
        rows = []
        for task in self.tasks.values():
            row = self.view(task, now, compact=True)
            if status != "all" and task.status != status:
                continue
            if not tags.issubset(task.tags):
                continue
            if text.casefold() not in (task.title + " " + task.notes + " " + task.next_action).casefold():
                continue
            if "overdue" in query and query["overdue"] != row["overdue"]:
                continue
            days = task_days(row, zone)
            if "undated" in query and query["undated"] != (not days):
                continue
            if (start or end) and not any((start is None or day >= start) and (end is None or day <= end) for day in days):
                continue
            rows.append(row)
        rows.sort(key=task_order)
        return {"tasks": rows[offset:offset + limit], "total": len(rows), "offset": offset, "limit": limit}

    def agenda(self, now, timezone_name, limit=12):
        zone = ZoneInfo(timezone_name)
        today = now.astimezone(zone).date()
        if type(limit) is not int or not 1 <= limit <= 30:
            raise ValueError("agenda limit must be 1..30")
        rows = sorted((self.view(task, now, compact=True) for task in self.tasks.values()
                       if task.status == "active"), key=task_order)
        relevant = []
        for row in rows:
            days = task_days(row, zone)
            if row["overdue"] or row["last_error"] or any(day <= today for day in days):
                relevant.append(row)
        return {"now": iso(now), "timezone": timezone_name, "date": today.isoformat(), "tasks": [{k: v for k, v in row.items() if v is not None and v != "" and v != []}
                  for row in relevant[:limit]], "total_relevant": len(relevant), "active_total": sum(t.status == "active" for t in self.tasks.values()), "truncated": len(relevant) > limit, "lookup": "scheduler.find_tasks"}

    def prepare(self, now):
        prepared = []
        for task in self.tasks.values():
            if task.status != "active":
                continue
            due = [key for key, when in self.events(task) if when <= now and key not in task.notified]
            if not due:
                continue
            if task.pending is None:
                task.pending = {"id": f"task-{task.id}-{task.revision}-{task.delivery_attempt}", "keys": due}
            else:
                task.pending["keys"] = sorted(set(task.pending["keys"] + due))
            prepared.append((task, task.pending["id"]))
        return prepared

    def observe(self, command_id, error):
        for task in self.tasks.values():
            if not task.pending or task.pending["id"] != command_id:
                continue
            task.last_error = error
            task.delivery_attempt += 1
            task.delivery_failures += int(error is not None)
            if error is None or task.delivery_failures >= 3:
                task.notified = sorted(set(task.notified + task.pending["keys"]))
                task.delivery_failures = 0
            task.pending = None
            # Delivery completion never resolves the user's task.
            return True
        return False
