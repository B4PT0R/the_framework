import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from the_framework.agent import Agent
from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from the_framework.plugins.scheduler import SchedulerExtension
from the_framework.plugins.scheduler.calendar import calendar_schedule, next_calendar
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.scheduler.tasks import TaskBook, instant

UTC = timezone.utc
NOW = instant("2026-10-19T10:00:00+02:00")


def book_task(**changes):
    book = TaskBook()
    task = book.create("tax", {"title": "Déposer le dossier", "timezone": "Europe/Paris", "tags": ["Admin"], **changes}, NOW)
    return book, task


def test_undated_task_stays_active_until_explicit_resolution():
    book, task = book_task()
    assert not task["overdue"] and task["next_reminder_at"] is None
    assert book.prepare(NOW + timedelta(days=50)) == []
    assert book.find({"undated": True, "tags": ["admin"]}, NOW)["total"] == 1
    done = book.resolve("tax", "done", 1, NOW)
    assert done["status"] == "done"
    assert book.find({}, NOW)["total"] == 0
    assert book.find({"status": "done"}, NOW)["total"] == 1


def test_planned_time_is_not_a_deadline_and_delivery_is_not_completion():
    book, task = book_task(planned_at="2026-10-19T13:00:00+02:00", reminder_at="2026-10-19T13:00:00+02:00")
    late = NOW + timedelta(hours=4)
    assert task["due_at"] is None
    assert len(book.prepare(late)) == 1
    pending = book.get("tax").pending["id"]
    assert book.observe(pending, None)
    assert book.prepare(late) == []
    assert book.get("tax").status == "active"
    assert book.view(book.get("tax"), late)["overdue"] is False


def test_date_only_deadline_expires_at_local_midnight_not_utc_midnight():
    book, _ = book_task(due_at="2026-10-25")
    task = book.get("tax")
    assert not book.view(task, instant("2026-10-25T23:59:59+01:00"))["overdue"]
    assert book.view(task, instant("2026-10-26T00:00:00+01:00"))["overdue"]


def test_advance_reminders_coalesce_after_downtime_and_overdue_is_visible():
    book, _ = book_task(due_at="2026-10-19T13:00:00+02:00", remind_before_minutes=[60, 15, 0], overdue_after_minutes=30)
    assert book.prepare(NOW) == []
    late = instant("2026-10-19T14:00:00+02:00")
    prepared = book.prepare(late)
    assert len(prepared) == 1 and len(book.get("tax").pending["keys"]) == 4
    book.observe(prepared[0][1], None)
    assert book.prepare(late) == []
    assert book.agenda(late, "Europe/Paris")["tasks"][0]["overdue"]


def test_snooze_keeps_deadline_but_postpone_changes_it_and_rejects_stale_resolution():
    book, _ = book_task(due_at="2026-10-19T11:00:00+02:00", remind_before_minutes=[30, 0], overdue_after_minutes=15)
    original = book.get("tax").due_at
    snoozed = book.resolve("tax", "snooze", 1, NOW, at="2026-10-19T12:00:00+02:00")
    assert snoozed["due_at"] == original
    assert book.prepare(instant("2026-10-19T11:30:00+02:00")) == []
    with pytest.raises(ValueError, match="revision"):
        book.resolve("tax", "done", 1, NOW)
    postponed = book.resolve("tax", "postpone", 2, NOW, at="2026-10-20T12:00:00+02:00")
    assert postponed["due_at"].startswith("2026-10-20")
    assert postponed["history"][-1]["due_at"] == original


def test_closing_or_editing_invalidates_pending_reminder():
    book, _ = book_task(reminder_at="2026-10-19T09:00:00+02:00")
    _, command_id = book.prepare(NOW)[0]
    book.update("tax", {"reminder_at": "2026-10-20T09:00:00+02:00"}, 1, NOW)
    assert not book.observe(command_id, None)
    assert book.prepare(NOW) == []
    book.resolve("tax", "cancel", 2, NOW)
    assert book.prepare(NOW + timedelta(days=2)) == []


def test_recurring_occurrence_requires_resolution_then_advances_without_backlog():
    book, task = book_task(recurrence={"time": "13:00", "timezone": "Europe/Paris", "weekdays": [0, 1, 2]}, remind_before_minutes=[0])
    assert task["due_at"] == "2026-10-19T11:00:00.000+00:00"
    later = instant("2026-10-23T16:00:00+02:00")
    assert book.view(book.get("tax"), later)["overdue"]
    advanced = book.resolve("tax", "done", 1, later)
    assert advanced["status"] == "active"
    assert advanced["occurrence_id"] != task["occurrence_id"]
    assert advanced["due_at"] == "2026-10-26T12:00:00.000+00:00"
    assert advanced["history"][-1]["action"] == "done"
    closed = book.resolve("tax", "cancel", 2, later, scope="series")
    assert closed["status"] == "cancelled"


@pytest.mark.parametrize("after,expected", [
    ("2026-10-24T08:30:00+02:00", "2026-10-25T07:30:00+00:00"),
    ("2026-03-28T08:30:00+01:00", "2026-03-29T06:30:00+00:00"),
])
def test_calendar_preserves_wall_clock_across_dst(after, expected):
    schedule = {"time": "08:30", "timezone": "Europe/Paris"}
    assert next_calendar(schedule, instant(after)) == instant(expected)


def test_calendar_gap_moves_forward_and_fold_does_not_fire_twice():
    schedule = {"time": "02:30", "timezone": "Europe/Paris"}
    assert next_calendar(schedule, instant("2026-03-28T03:00:00+01:00")) == instant("2026-03-29T03:30:00+02:00")
    assert next_calendar(schedule, instant("2026-10-25T02:45:00+02:00")) == instant("2026-10-26T02:30:00+01:00")


@pytest.mark.parametrize("payload", [
    {"due_at": "2026-10-19T12:00:00"}, {"priority": True}, {"remind_before_minutes": [10]},
    {"unknown": 2}, {"tags": [""]}, {"overdue_after_minutes": 0},
    {"recurrence": {"time": "12:00", "timezone": "UTC"}},
])
def test_invalid_tasks_are_rejected(payload):
    with pytest.raises((ValueError, TypeError)):
        book_task(**payload)


def test_query_filters_period_theme_and_pagination():
    book, _ = book_task(due_at="2026-10-19")
    book.create("shop", {"title": "Courses", "tags": ["maison"], "due_at": "2026-10-22"}, NOW)
    book.create("idea", {"title": "Une idée"}, NOW)
    assert book.find({"from_date": "2026-10-19", "to_date": "2026-10-19", "timezone": "Europe/Paris"}, NOW)["total"] == 1
    assert book.find({"text": "COURSES"}, NOW)["tasks"][0]["id"] == "shop"
    page = book.find({"limit": 1, "offset": 1}, NOW)
    assert page["total"] == 3 and len(page["tasks"]) == 1


def test_agenda_is_bounded_and_empty_agenda_is_not_an_empty_task_list():
    book = TaskBook()
    for n in range(25):
        book.create(str(n), {"title": f"Task {n}", "due_at": "2026-10-19"}, NOW)
    book.create("later", {"title": "No deadline"}, NOW)
    agenda = book.agenda(NOW, "Europe/Paris", limit=5)
    assert agenda["active_total"] == 26 and agenda["total_relevant"] == 25
    assert agenda["truncated"] and len(agenda["tasks"]) == 5


def test_task_service_persistence_retries_and_existing_wakes(tmp_path, monkeypatch):
    async def test():
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: NOW)
        sent = []
        async def deliver(command):
            sent.append(command)
        service = SchedulerService(tmp_path / "scheduler.json", deliver, retry_interval=0)
        await service.create(wake_id="old", name="Existing wake", prompt="Leave it intact", at="2026-10-19T13:00:00+02:00")
        await service.task_call("create", {"task_id": "tax", "task": {"title": "Dossier", "reminder_at": "2026-10-19T09:00:00+02:00"}})
        await service.dispatch_due(NOW)
        first = sent[-1]
        assert "scheduled_task" in first.prompt
        await service.observe(CommandFailed(request_id=first.id, command=first, error="interrupted"))
        await service.dispatch_due(NOW)
        second = sent[-1]
        assert first.id != second.id
        await service.observe(CommandCompleted(request_id=second.id, command=second))
        assert service.tasks.get("tax").status == "active"
        await service.dispatch_due(NOW)
        assert len(sent) == 2
        restored = SchedulerService(service.path, deliver)
        restored.wakes = restored._load()
        assert restored.tasks.get("tax").status == "active"
        assert restored.wakes["old"].enabled
        await restored.dispatch_due(NOW)
        assert len(sent) == 2
    asyncio.run(test())


def test_failed_mutation_rolls_back_memory_and_keeps_persisted_task(tmp_path, monkeypatch):
    async def test():
        service = SchedulerService(tmp_path / "scheduler.json", None)
        original = await service.task_call("create", {"task_id": "tax", "task": {"title": "Dossier"}})
        with pytest.raises(ValueError):
            await service.task_call("update", {"task_id": "tax", "changes": {"priority": 99}, "expected_revision": 1})
        assert service.tasks.get("tax").revision == 1
        def fail():
            raise OSError("disk full")
        monkeypatch.setattr(service, "_save", fail)
        with pytest.raises(OSError):
            await service.task_call("resolve", {"task_id": "tax", "action": "done", "expected_revision": 1})
        assert service.tasks.get("tax").status == "active"
        assert service.tasks.get("tax").revision == original["revision"]
    asyncio.run(test())


def test_calendar_wakes_advance_in_server_without_agent_reprogramming(tmp_path, monkeypatch):
    async def test():
        now = instant("2026-10-24T07:00:00+02:00")
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: now)
        sent = []
        async def deliver(command):
            sent.append(command)
        service = SchedulerService(tmp_path / "scheduler.json", deliver)
        wake = await service.create(wake_id="morning", name="Morning", prompt="Review tasks", calendar={"time": "08:30", "timezone": "Europe/Paris"})
        assert wake["schedule_type"] == "calendar"
        now = instant("2026-10-24T08:31:00+02:00")
        await service.dispatch_due(now)
        command = sent[0]
        await service.observe(CommandCompleted(request_id=command.id, command=command))
        assert service.wakes["morning"].next_run_at == "2026-10-25T07:30:00.000+00:00"
    asyncio.run(test())


def test_plugin_task_tools_and_live_provider_use_server_bridge():
    async def test():
        agent = Agent(client=object(), scheduler={"timezone": "Europe/Paris"})
        service_calls = []
        async def request(payload):
            service_calls.append(payload)
            return {"ok": True}
        agent.bridge.bind(request)
        plugin = agent.add_extension(SchedulerExtension)
        await plugin.create_task('{"title": "Test"}')
        assert service_calls[-1].payload["task"]["timezone"] == "Europe/Paris"
        await plugin.daily_tasks()
        assert service_calls[-1].method == "task_agenda"
        assert service_calls[-1].payload["timezone"] == "Europe/Paris"
    asyncio.run(test())


def test_period_matches_deadline_even_when_planned_for_another_day():
    book, _ = book_task(due_at="2026-10-22", planned_at="2026-10-19T13:00:00+02:00")
    for day in ("2026-10-19", "2026-10-22"):
        assert book.find({"from_date": day, "to_date": day, "tags": ["ADMIN"]}, NOW)["total"] == 1
    assert book.find({"from_date": "2026-10-20", "to_date": "2026-10-21"}, NOW)["total"] == 0


def test_agenda_counts_all_relevant_tasks_beyond_search_page_size():
    book = TaskBook()
    for n in range(120):
        book.create(str(n), {"title": f"Task {n}", "due_at": "2026-10-19"}, NOW)
    agenda = book.agenda(NOW, "Europe/Paris")
    assert agenda["total_relevant"] == 120
    assert len(agenda["tasks"]) == 12 and agenda["truncated"]


@pytest.mark.parametrize("query", [{"tags": "admin"}, {"text": 4}, {"overdue": 1}, {"undated": None}, {"limit": True}, {"offset": -1}])
def test_invalid_queries_are_rejected(query):
    book, _ = book_task()
    with pytest.raises((TypeError, ValueError)):
        book.find(query, NOW)


@pytest.mark.parametrize("time", ["8:30", "0830", "08:30:00", "08:30Z", "08:30+02:00"])
def test_calendar_requires_unambiguous_hour_and_minute(time):
    with pytest.raises(ValueError):
        calendar_schedule({"time": time, "timezone": "Europe/Paris"})


def test_recurrence_skip_scope_and_closed_series_are_explicit():
    book, _ = book_task(recurrence={"time": "13:00", "timezone": "Europe/Paris"})
    with pytest.raises(ValueError):
        book.resolve("tax", "skip", 1, NOW, scope="series")
    skipped = book.resolve("tax", "skip", 1, NOW)
    assert skipped["status"] == "active" and skipped["due_at"].startswith("2026-10-20")
    closed = book.resolve("tax", "done", 2, NOW, scope="series")
    assert closed["status"] == "done"
    with pytest.raises(ValueError):
        book.resolve("tax", "done", 3, NOW)


def test_queued_reminders_coalesce_and_failures_are_bounded_per_batch():
    book, _ = book_task(due_at="2026-10-19T11:00:00+02:00", remind_before_minutes=[60, 30, 0])
    _, first_id = book.prepare(NOW)[0]
    assert len(book.get("tax").pending["keys"]) == 1
    book.prepare(NOW + timedelta(minutes=30))
    assert len(book.get("tax").pending["keys"]) == 2
    assert book.observe(first_id, None)
    # A later batch retains its own full retry budget, regardless of earlier successes.
    later = NOW + timedelta(hours=1)
    for _ in range(3):
        _, command_id = book.prepare(later)[0]
        assert book.observe(command_id, "worker failure")
    assert book.prepare(later) == []
    assert book.get("tax").status == "active"
    assert book.agenda(later, "Europe/Paris")["tasks"][0]["last_error"] == "worker failure"


def test_restart_reuses_pending_command_id_after_uncertain_delivery(tmp_path):
    async def test():
        sent = []
        async def uncertain(command):
            sent.append(command)
            raise OSError("transport lost after enqueue")
        service = SchedulerService(tmp_path / "scheduler.json", uncertain)
        await service.task_call("create", {"task_id": "tax", "task": {"title": "Dossier", "reminder_at": "2026-10-19T09:00:00+02:00"}})
        await service.dispatch_due(NOW)
        restored = SchedulerService(service.path, uncertain)
        restored.wakes = restored._load()
        await restored.dispatch_due(NOW)
        assert len(sent) == 2 and sent[0].id == sent[1].id
        task = await restored.task_call("get", {"task_id": "tax"})
        await restored.task_call("resolve", {"task_id": "tax", "action": "done", "expected_revision": task["revision"]})
        await restored.observe(CommandCompleted(request_id=sent[-1].id, command=sent[-1]))
        await restored.dispatch_due(NOW + timedelta(days=1))
        assert len(sent) == 2 and restored.tasks.get("tax").status == "done"
    asyncio.run(test())


def test_existing_wake_only_document_and_corrupt_task_state(tmp_path):
    import json
    async def test():
        path = tmp_path / "scheduler.json"
        original = SchedulerService(path, None)
        await original.create(wake_id="legacy", name="Keep", prompt="Keep", at="2026-10-19T13:00:00+02:00")
        document = json.loads(path.read_text())
        del document["tasks"]
        path.write_text(json.dumps(document))
        restored = SchedulerService(path, None)
        assert restored._load()["legacy"].enabled
        assert restored.tasks.snapshot() == []
        document["tasks"] = [{"title": "Incomplete"}]
        path.write_text(json.dumps(document))
        await restored.start()
        try:
            assert restored.error is not None
            with pytest.raises(RuntimeError):
                await restored.task_call("create", {"task_id": "new", "task": {"title": "New"}})
            assert json.loads(path.read_text()) == document
        finally:
            await restored.stop()
    asyncio.run(test())


def test_task_tool_schemas_and_unavailable_context_are_honest():
    async def test():
        agent = Agent(client=object())
        plugin = agent.add_extension(SchedulerExtension)
        namespace = agent.registry("tools")["scheduler"]
        for name, field in (("create_task", "task_json"), ("update_task", "changes_json"), ("find_tasks", "query_json")):
            tool = namespace.find(name)
            assert tool.parameters.properties[field].type == "string"
            assert tool.description
        assert namespace.find("create_wake").parameters.properties.calendar_json.type == ["string", "null"]
        context = await plugin.daily_tasks()
        assert context["status"] == "unavailable" and "not empty" in context["warning"]
        for payload in ("[]", "null", "bad json"):
            with pytest.raises(ValueError, match="JSON object"):
                await plugin.create_task(payload)
        outputs = await agent.registry("providers")["daily_tasks"].aoutputs()
        assert outputs and "unavailable" in outputs[0].content[0].text
    asyncio.run(test())


def test_tool_metadata_survives_python_docstring_dedenting():
    # Python 3.13+ dedents compiled docstrings; YAML block indentation must survive.
    import inspect
    from the_framework.agent.extensions.schema import parse_yaml_docstring
    for name, method in inspect.getmembers(SchedulerExtension, inspect.isfunction):
        if method.__doc__ and "description: >-" in method.__doc__:
            metadata = parse_yaml_docstring(inspect.cleandoc(method.__doc__))
            assert metadata["description"].strip(), name
