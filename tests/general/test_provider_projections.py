"""Live projections are concise without mutating authoritative records."""
import asyncio
import json
from copy import deepcopy

from the_framework.plugins.agentic.service import AgenticService
from the_framework.plugins.browser import ChromiumExtension
from the_framework.plugins.scheduler.tasks import TaskBook, instant


def test_goal_dashboard_keeps_constraints_and_active_evidence(tmp_path):
    async def run():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await service.handle("create", {
            "objective": "Ship the feature", "scope": "Local only; no push",
            "success_criteria": ["Tests and live verification"], "tasks": ["Inspect", "Implement"],
        })
        tasks = deepcopy(goal.tasks)
        tasks[0].update(status="completed", evidence="Read source " * 100)
        tasks[1].update(status="in_progress", evidence="Resume from src/example.py")
        goal = await service.handle("progress", {"goal_id": goal.id, "expected_revision": goal.revision,
            "changes": {"tasks": tasks, "summary": "Inspection done"}})
        before = deepcopy(service.goals)
        state = await service.handle("current", {})
        dashboard = state["goal"]
        assert not state["truncated"] and "get_goal" in state["lookup"]
        for key in ("id", "revision", "objective", "scope", "success_criteria", "summary"):
            assert dashboard[key] == goal[key]
        assert "pending" not in dashboard and "created_at" not in dashboard
        assert "evidence" not in dashboard["tasks"][0]
        assert dashboard["tasks"][1]["evidence"] == tasks[1].evidence
        full = await service.handle("get", {"goal_id": goal.id})
        assert full.tasks[0].evidence == tasks[0].evidence
        assert service.goals == before
        suspended = await service.handle("suspend", {"goal_id": goal.id,
            "expected_revision": goal.revision, "reason": "Need approval; ask user"})
        state = await service.handle("current", {})
        assert state["goal"]["outcome"] == suspended.outcome
        assert not state["automatic_continuation"]
    asyncio.run(run())


def test_oversized_goal_projection_has_constraints_lookup_and_task_count(tmp_path):
    async def run():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await service.handle("create", {
            "objective": "o" * 16000, "scope": "s" * 16000,
            "success_criteria": ["c" * 2000] * 30, "tasks": ["t" * 2000] * 100,
        })
        state = await service.handle("current", {})
        assert state["truncated"] and "before acting" in state["lookup"]
        assert state["goal"]["scope"] and len(state["goal"]["success_criteria"]) == 30
        assert state["goal"]["task_total"] == 100
        assert len(json.dumps(state, ensure_ascii=False)) < 20000
        assert len((await service.handle("get", {"goal_id": goal.id})).scope) == 16000
    asyncio.run(run())


def test_browser_long_fields_keep_both_ends_and_explicit_lookup():
    value = "HEAD" + "x" * 20000 + "TAIL"
    snapshot = {"elements": [{"id": "fresh-1", "role": "textarea", "name": "Draft", "value": value},
                             {"id": "fresh-2", "role": "link", "href": value, "placeholder": value}]}
    before = deepcopy(snapshot)
    result = ChromiumExtension._format_snapshot("web", snapshot)
    assert "HEAD" in result and "TAIL" in result
    assert "20008 chars; use read_element" in result
    assert len(result) < 2500
    assert snapshot == before
    assert ChromiumExtension._preview("ordinary value") == "ordinary value"
    assert ChromiumExtension._preview("x" * 600) == "x" * 600


def test_agenda_only_omits_empty_fields_and_preserves_false_flags():
    now = instant("2026-10-23T08:00:00+02:00")
    book = TaskBook()
    book.create("today", {"title": "Act today", "planned_at": "2026-10-23T10:00:00+02:00",
                          "next_action": "Review file", "remind_before_minutes": [0]}, now)
    before = deepcopy(book.tasks)
    agenda = book.agenda(now, "Europe/Paris")
    row = agenda["tasks"][0]
    assert "due_at" not in row and "last_error" not in row and "tags" not in row
    assert row["overdue"] is False and row["priority"] == 0
    assert row["next_action"] == "Review file" and row["next_reminder_at"]
    assert agenda["active_total"] == agenda["total_relevant"] == 1
    assert agenda["timezone"] == "Europe/Paris" and not agenda["truncated"]
    assert book.tasks == before
    assert book.view(book.get("today"), now)["due_at"] is None
