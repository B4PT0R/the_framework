"""Fixed annual dates share the same persistent calendar API as monthly rules."""
import asyncio

import pytest

from the_framework.agent.runtime.protocol import CommandCompleted
from the_framework.plugins.scheduler.calendar import calendar_schedule, next_calendar
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.scheduler.tasks import instant

BASE = {"time": "10:00", "timezone": "Europe/Paris"}


@pytest.mark.parametrize("dates,after,expected", [
    (["12-25"], "2026-10-21T10:00+02:00", "2026-12-25T10:00+01:00"),
    (["12-25"], "2026-12-25T09:59+01:00", "2026-12-25T10:00+01:00"),
    (["12-25"], "2026-12-25T10:00+01:00", "2027-12-25T10:00+01:00"),
    (["01-01"], "2026-12-31T23:59+01:00", "2027-01-01T10:00+01:00"),
    (["07-14"], "2026-07-14T10:00+02:00", "2027-07-14T10:00+02:00"),
    (["12-25", "01-01"], "2026-12-25T10:00+01:00", "2027-01-01T10:00+01:00"),
    (["12-25", "01-01"], "2027-01-01T10:00+01:00", "2027-12-25T10:00+01:00"),
    (["12-25", "12-25"], "2026-12-25T10:00+01:00", "2027-12-25T10:00+01:00"),
    (["02-29"], "2026-10-21T10:00+02:00", "2028-02-29T10:00+01:00"),
    (["02-29"], "2028-02-29T10:00+01:00", "2032-02-29T10:00+01:00"),
    (["02-29"], "2096-02-29T10:00+01:00", "2104-02-29T10:00+01:00"),
    (["02-29"], "2399-03-01T10:00+01:00", "2400-02-29T10:00+01:00"),
    (["02-29", "03-01"], "2027-02-28T10:00+01:00", "2027-03-01T10:00+01:00"),
    (["02-29", "03-01"], "2028-02-28T10:00+01:00", "2028-02-29T10:00+01:00"),
])
def test_annual_occurrences(dates, after, expected):
    assert next_calendar({**BASE, "dates": dates}, instant(after)) == instant(expected)


@pytest.mark.parametrize("dates", [
    None, [], "12-25", [None], [True], [1225], [12.25], [[12, 25]], [{}],
    [""], ["2-29"], ["02-9"], ["02-30"], ["04-31"], ["00-01"], ["13-01"],
    ["01-00"], ["01-32"], ["2026-12-25"], ["12-25T10:00"], [" 12-25"],
    ["12-25 "], ["0229"], ["12-25", "02-30"],
])
def test_invalid_annual_dates_are_rejected(dates):
    with pytest.raises(ValueError, match="dates"):
        calendar_schedule({**BASE, "dates": dates})


@pytest.mark.parametrize("selector", [
    {"weekdays": [0]}, {"monthdays": [25]}, {"weekday_ordinals": [1]},
    {"weekdays": [5], "weekday_ordinals": [1]},
])
def test_annual_dates_exclude_other_date_selectors(selector):
    with pytest.raises(ValueError, match="dates cannot be combined"):
        calendar_schedule({**BASE, "dates": ["12-25"], **selector})


@pytest.mark.parametrize("dates,after,expected", [
    (["03-29"], "2026-03-01T00:00+01:00", "2026-03-29T03:30+02:00"),
    (["10-25"], "2026-10-01T00:00+02:00", "2026-10-25T02:30+02:00"),
    (["10-25"], "2026-10-25T02:45+02:00", "2027-10-25T02:30+02:00"),
])
def test_annual_dst_gap_and_fold(dates, after, expected):
    rule = {**BASE, "time": "02:30", "dates": dates}
    assert next_calendar(rule, instant(after)) == instant(expected)


def test_annual_date_uses_local_year_and_date():
    rule = {"time": "00:15", "timezone": "Pacific/Auckland", "dates": ["01-01"]}
    assert next_calendar(rule, instant("2026-12-31T11:00Z")) == instant("2026-12-31T11:15Z")
    assert next_calendar(rule, instant("2026-12-31T11:15Z")) == instant("2027-12-31T11:15Z")


def test_annual_date_at_end_of_supported_datetime_range():
    rule = {"time": "10:00", "timezone": "UTC", "dates": ["12-25"]}
    assert next_calendar(rule, instant("9999-12-24T10:00Z")) == instant("9999-12-25T10:00Z")
    with pytest.raises(ValueError, match="no next occurrence"):
        next_calendar(rule, instant("9999-12-25T10:00Z"))


@pytest.mark.parametrize("dates,first,next_due", [
    (["12-25"], "2026-12-25T09:00:00.000+00:00", "2030-12-25T09:00:00.000+00:00"),
    (["02-29"], "2028-02-29T09:00:00.000+00:00", "2032-02-29T09:00:00.000+00:00"),
])
def test_annual_wake_and_task_reload_advance_and_reschedule(tmp_path, monkeypatch, dates, first, next_due):
    async def test():
        now = instant("2026-10-21T10:00+02:00")
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: now)
        sent = []
        async def deliver(command):
            sent.append(command)
        path = tmp_path / "scheduler.json"
        rule = {**BASE, "dates": dates}
        service = SchedulerService(path, deliver)
        wake = await service.create(wake_id="annual", name="Annual", prompt="Check", calendar=rule)
        task = await service.task_call("create", {"task_id": "annual", "task": {
            "title": "Annual", "timezone": "Europe/Paris", "recurrence": rule}})
        assert wake["next_run_at"] == task["due_at"] == first
        restored = SchedulerService(path, deliver)
        await restored.start()
        try:
            assert restored.wakes["annual"].calendar == rule
            assert restored.tasks.get("annual").recurrence == rule
            # Several missed years coalesce into one wake; tasks wait for resolution.
            now = instant("2030-03-01T12:00+01:00")
            await restored.dispatch_due(now)
            assert len(sent) == 1
            command = sent[0]
            await restored.observe(CommandCompleted(request_id=command.id, command=command))
            assert restored.wakes["annual"].next_run_at == next_due
            assert restored.tasks.get("annual").due_at == first
            advanced = await restored.task_call("resolve", {
                "task_id": "annual", "action": "done", "expected_revision": 1})
            assert advanced["due_at"] == next_due
            await restored.dispatch_due(now)
            assert len(sent) == 1
            await restored.reschedule(wake_id="annual", calendar={**BASE, "dates": ["01-01"]})
            assert restored.wakes["annual"].next_run_at == "2031-01-01T09:00:00.000+00:00"
        finally:
            await restored.stop()
        reloaded = SchedulerService(path, None)
        await reloaded.start()
        try:
            assert reloaded.wakes["annual"].calendar == {**BASE, "dates": ["01-01"]}
            assert reloaded.wakes["annual"].next_run_at == "2031-01-01T09:00:00.000+00:00"
            assert reloaded.tasks.get("annual").recurrence == rule
            assert reloaded.tasks.get("annual").due_at == next_due
        finally:
            await reloaded.stop()
    asyncio.run(test())
