"""Calendar rules are shared by server wakes and tracked-task occurrences."""
import asyncio

import pytest

from the_framework.agent.runtime.protocol import CommandCompleted
from the_framework.plugins.scheduler.calendar import calendar_schedule, next_calendar
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.scheduler.tasks import TaskBook, instant

BASE = {"time": "10:00", "timezone": "Europe/Paris"}
FIRST_SATURDAY = {**BASE, "weekdays": [5], "weekday_ordinals": [1]}


@pytest.mark.parametrize("fields,after,expected", [
    ({"weekdays": [5], "weekday_ordinals": [1]}, "2026-10-21T09:00+02:00", "2026-11-07T10:00+01:00"),
    ({"weekdays": [5], "weekday_ordinals": [1]}, "2026-11-07T09:59+01:00", "2026-11-07T10:00+01:00"),
    ({"weekdays": [5], "weekday_ordinals": [1]}, "2026-11-07T10:00+01:00", "2026-12-05T10:00+01:00"),
    ({"weekdays": [4], "weekday_ordinals": [-1]}, "2026-10-21T09:00+02:00", "2026-10-30T10:00+01:00"),
    ({"weekdays": [0], "weekday_ordinals": [2]}, "2026-12-31T23:00+01:00", "2027-01-11T10:00+01:00"),
    ({"weekdays": [0], "weekday_ordinals": [5]}, "2027-02-01T09:00+01:00", "2027-03-29T10:00+02:00"),
    ({"weekdays": [0, 5], "weekday_ordinals": [1, 3]}, "2026-11-07T10:00+01:00", "2026-11-16T10:00+01:00"),
    ({"weekdays": [5], "weekday_ordinals": [-1, 5]}, "2026-10-31T10:00+01:00", "2026-11-28T10:00+01:00"),
    ({"monthdays": [15]}, "2026-12-15T10:00+01:00", "2027-01-15T10:00+01:00"),
    ({"monthdays": [31]}, "2026-01-31T10:00+01:00", "2026-03-31T10:00+02:00"),
    ({"monthdays": [29]}, "2027-01-29T10:00+01:00", "2027-03-29T10:00+02:00"),
    ({"monthdays": [29]}, "2028-01-29T10:00+01:00", "2028-02-29T10:00+01:00"),
    ({"monthdays": [-1]}, "2027-02-01T10:00+01:00", "2027-02-28T10:00+01:00"),
    ({"monthdays": [-1]}, "2028-02-01T10:00+01:00", "2028-02-29T10:00+01:00"),
    ({"monthdays": [-1, 31]}, "2026-10-31T10:00+01:00", "2026-11-30T10:00+01:00"),
    ({"monthdays": [1, 15]}, "2026-11-01T10:00+01:00", "2026-11-15T10:00+01:00"),
])
def test_monthly_occurrences(fields, after, expected):
    assert next_calendar({**BASE, **fields}, instant(after)) == instant(expected)


@pytest.mark.parametrize("after,expected", [
    ("2026-03-01T00:00+01:00", "2026-03-29T03:30+02:00"),
    ("2026-10-01T00:00+02:00", "2026-10-25T02:30+02:00"),
    ("2026-10-25T02:45+02:00", "2026-11-29T02:30+01:00"),
])
def test_monthly_dst_gap_and_fold(after, expected):
    rule = {**BASE, "time": "02:30", "weekdays": [6], "weekday_ordinals": [-1]}
    assert next_calendar(rule, instant(after)) == instant(expected)


def test_date_matching_uses_local_date_not_utc_date():
    rule = {"time": "00:15", "timezone": "Pacific/Auckland", "monthdays": [1]}
    assert next_calendar(rule, instant("2026-10-31T10:00+00:00")) == instant("2026-10-31T11:15+00:00")


@pytest.mark.parametrize("fields", [
    {"monthdays": []}, {"monthdays": [0]}, {"monthdays": [32]}, {"monthdays": [-2]},
    {"monthdays": [True]}, {"monthdays": [1.0]}, {"monthdays": None},
    {"monthdays": [15], "weekdays": [0]}, {"monthdays": [1], "weekday_ordinals": [1]},
    {"weekday_ordinals": [1]}, {"weekdays": [], "weekday_ordinals": [1]},
    {"weekdays": [7]}, {"weekdays": [False]}, {"weekdays": "5"},
    {"weekdays": [5], "weekday_ordinals": []}, {"weekdays": [5], "weekday_ordinals": [0]},
    {"weekdays": [5], "weekday_ordinals": [6]}, {"weekdays": [5], "weekday_ordinals": [-2]},
    {"weekdays": [5], "weekday_ordinals": [True]}, {"weekdays": [5], "weekday_ordinals": None},
    {"months": [1]}, {"timezone": "Not/AZone"},
])
def test_invalid_rules_are_rejected(fields):
    with pytest.raises(ValueError):
        calendar_schedule({**BASE, **fields})


@pytest.mark.parametrize("fields", [
    {}, {"weekdays": [5, 0, 5]}, {"monthdays": [31, -1, 1, 31]},
    {"weekdays": [5, 5], "weekday_ordinals": [3, 1, -1, 1]},
    {"dates": ["12-25", "02-29", "01-01", "12-25"]},
])
def test_normalization_is_idempotent_and_does_not_mutate_input(fields):
    source = {**BASE, **fields}
    before = repr(source)
    rule = calendar_schedule(source)
    assert calendar_schedule(rule) == rule
    assert repr(source) == before
    for field in ("weekdays", "monthdays", "weekday_ordinals", "dates"):
        if field in rule:
            assert rule[field] == sorted(set(rule[field]))


def test_monthly_wake_persists_reschedules_and_advances_without_backlog(tmp_path, monkeypatch):
    async def test():
        now = instant("2026-10-21T09:00+02:00")
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: now)
        sent = []
        async def deliver(command):
            sent.append(command)
        path = tmp_path / "scheduler.json"
        service = SchedulerService(path, deliver)
        wake = await service.create(wake_id="monthly", name="Monthly", prompt="Check", calendar=FIRST_SATURDAY)
        assert wake["next_run_at"] == "2026-11-07T09:00:00.000+00:00"
        restored = SchedulerService(path, deliver)
        await restored.start()
        try:
            assert restored.wakes["monthly"].calendar == calendar_schedule(FIRST_SATURDAY)
            # Months of downtime coalesce into one delivery, not a monthly backlog.
            now = instant("2027-02-20T12:00+01:00")
            await restored.dispatch_due(now)
            assert len(sent) == 1
            command = sent[0]
            await restored.observe(CommandCompleted(request_id=command.id, command=command))
            assert restored.wakes["monthly"].next_run_at == "2027-03-06T09:00:00.000+00:00"
            await restored.dispatch_due(now)
            assert len(sent) == 1
            await restored.reschedule(wake_id="monthly", calendar={**BASE, "monthdays": [-1]})
            assert restored.wakes["monthly"].next_run_at == "2027-02-28T09:00:00.000+00:00"
        finally:
            await restored.stop()
        reloaded = SchedulerService(path, None)
        await reloaded.start()
        try:
            assert reloaded.wakes["monthly"].calendar == {**BASE, "monthdays": [-1]}
            assert reloaded.wakes["monthly"].next_run_at == "2027-02-28T09:00:00.000+00:00"
        finally:
            await reloaded.stop()
    asyncio.run(test())


def test_monthly_tracked_task_advances_only_on_explicit_resolution():
    now = instant("2026-10-21T09:00+02:00")
    book = TaskBook()
    first = book.create("monthly", {"title": "Monthly", "timezone": "Europe/Paris", "recurrence": FIRST_SATURDAY}, now)
    assert first["due_at"] == "2026-11-07T09:00:00.000+00:00"
    later = instant("2027-02-20T12:00+01:00")
    assert book.view(book.get("monthly"), later)["overdue"]
    assert book.get("monthly").due_at == first["due_at"]
    advanced = book.resolve("monthly", "done", 1, later)
    assert advanced["due_at"] == "2027-03-06T09:00:00.000+00:00"
    assert advanced["occurrence_id"] != first["occurrence_id"]
    skipped = book.resolve("monthly", "skip", 2, later)
    assert skipped["due_at"] == "2027-04-03T08:00:00.000+00:00"
    closed = book.resolve("monthly", "cancel", 3, later, scope="series")
    assert closed["status"] == "cancelled"


def test_monthly_task_survives_service_reload(tmp_path, monkeypatch):
    async def test():
        now = instant("2026-10-21T09:00+02:00")
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: now)
        path = tmp_path / "scheduler.json"
        service = SchedulerService(path, None)
        rule = {**BASE, "monthdays": [-1]}
        task = await service.task_call("create", {"task_id": "monthly", "task": {
            "title": "Monthly", "timezone": "Europe/Paris", "recurrence": rule}})
        restored = SchedulerService(path, None)
        await restored.start()
        try:
            assert restored.tasks.get("monthly").recurrence == rule
            assert restored.tasks.get("monthly").due_at == task["due_at"]
            advanced = await restored.task_call("resolve", {
                "task_id": "monthly", "action": "done", "expected_revision": 1})
            assert advanced["due_at"] == "2026-11-30T09:00:00.000+00:00"
        finally:
            await restored.stop()
    asyncio.run(test())
