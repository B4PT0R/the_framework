"""Persistent goals; the canonical worker remains the only execution owner."""

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timezone
from html import escape

from modict import modict

from ...agent.models.base import Base
from ...agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from ...utils.ids import timestamp_id
from ...utils.persistence import MappingStore


def text(value, name, maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be nonempty text, at most {maximum} characters")
    return value.strip()


def criteria(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 30:
        raise ValueError("success_criteria must contain 1..30 strings")
    return [text(item, "success criterion", 2000) for item in value]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class GoalTask(Base):
    _config = modict.config(enforce_json=True, extra="forbid")

    id: str
    title: str
    status: str = "pending"
    evidence: str = ""


class Goal(Base):
    _config = modict.config(enforce_json=True, extra="forbid")

    id: str
    objective: str
    scope: str
    success_criteria: list[str]
    tasks: list[GoalTask] = modict.factory(list)
    status: str = "active"
    revision: int = 1
    summary: str = ""
    outcome: str = ""
    user_instruction: str = ""
    created_at: str
    updated_at: str
    sequence: int = 0
    progress: int = 0
    stalled_turns: int = 0
    failures: int = 0
    pending: dict | None = None

    @modict.validator("tasks", mode="before")
    def reconstruct_tasks(self, value):
        return [GoalTask(**item) for item in value]


def validate_goal(goal):
    text(goal.id, "goal id", 200)
    text(goal.objective, "objective")
    text(goal.scope, "scope")
    criteria(goal.success_criteria)
    if goal.status not in {"active", "suspended", "completed", "cancelled"}:
        raise ValueError("invalid goal status")
    if not 1 <= len(goal.tasks) <= 100:
        raise ValueError("a goal must contain 1..100 tasks")
    ids = set()
    for task in goal.tasks:
        text(task.id, "task id", 200)
        text(task.title, "task title", 2000)
        if task.id in ids or task.status not in {"pending", "in_progress", "completed", "skipped"}:
            raise ValueError("duplicate task id or invalid task status")
        ids.add(task.id)
        if not isinstance(task.evidence, str) or len(task.evidence) > 8000:
            raise ValueError("task evidence must be text, at most 8000 characters")
        if task.status in {"completed", "skipped"}:
            text(task.evidence, "completed/skipped task evidence", 8000)
    if goal.status == "completed" and any(t.status not in {"completed", "skipped"} for t in goal.tasks):
        raise ValueError("complete or explicitly skip every task first")
    for key in ("revision", "sequence", "progress", "stalled_turns", "failures"):
        value = goal[key]
        if type(value) is not int or value < (1 if key == "revision" else 0):
            raise ValueError(f"invalid {key}")
    if goal.pending is not None:
        pending = goal.pending
        if set(pending) != {"id", "progress"} or not isinstance(pending["id"], str) or type(pending["progress"]) is not int:
            raise ValueError("invalid continuation receipt")


class AgenticService:
    """One open goal per session, with a durable, idempotent continuation outbox."""

    def __init__(self, path, deliver, *, enabled=lambda: True, poll_interval=1.0, retry_interval=5.0):
        self.store = MappingStore(path, field="goals")
        self.path = self.store.path
        self.deliver = deliver
        self.enabled = enabled
        self.poll_interval = poll_interval
        self.retry_interval = retry_interval
        self.goals = {}
        self.lock = asyncio.Lock()
        self.task = None
        self.error = None
        self.last_attempt = 0.0
        self.delivery_failures = 0

    def health(self):
        return {"status": "degraded" if self.error else "ok",
                "running": self.task is not None and not self.task.done(), "error": self.error,
                "open_goal": next((g.id for g in self.goals.values() if g.status in {"active", "suspended"}), None)}

    async def start(self):
        if self.task is not None and not self.task.done():
            return
        try:
            records = await asyncio.to_thread(self.store.load)
            goals = {key: Goal(**value) for key, value in records.items()}
            for key, goal in goals.items():
                validate_goal(goal)
                if key != goal.id:
                    raise ValueError("goal key does not match id")
            if sum(g.status in {"active", "suspended"} for g in goals.values()) > 1:
                raise ValueError("only one goal may remain open")
            self.goals, self.error = goals, None
        except Exception as error:
            self.error = f"could not load agentic state: {error}"
            return  # Fail closed: never replace unreadable persisted work.
        self.last_attempt = 0.0
        self.delivery_failures = 0
        self.task = asyncio.create_task(self._run(), name="agentic-goals")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _commit(self, records):
        try:
            await asyncio.to_thread(self.store.save, records)
        except BaseException:
            # Cancelling the thread await cannot cancel the write: replacement
            # may still happen. Reconcile disk on reload before dispatching again.
            self.error = "agentic persistence failed; reload required before continuing"
            raise
        self.goals = records

    def _goal(self, goal_id, revision=None):
        if goal_id not in self.goals:
            raise ValueError(f"unknown goal: {goal_id}")
        goal = self.goals[goal_id]
        if revision is not None and (type(revision) is not int or goal.revision != revision):
            raise ValueError("stale goal revision; read the goal before editing")
        return goal

    async def handle(self, method, payload, *, command_id=None):
        if self.error is not None:
            raise RuntimeError(self.error)
        async with self.lock:
            if method == "current":
                goal = next((g for g in self.goals.values() if g.status in {"active", "suspended"}), None)
                projection = None
                truncated = False
                if goal is not None:
                    projection = {k: deepcopy(goal[k]) for k in (
                        "id", "status", "revision", "objective", "scope", "success_criteria",
                        "summary", "outcome", "stalled_turns", "failures")}
                    projection["tasks"] = [
                        {**{k: t[k] for k in ("id", "title", "status")},
                         **({"evidence": t.evidence} if t.evidence and t.status in {"pending", "in_progress"} else {})}
                        for t in goal.tasks]
                    truncated = len(json.dumps(projection, ensure_ascii=False)) > 20000
                    if truncated:
                        for key in ("objective", "scope", "summary", "outcome"):
                            projection[key] = projection[key][:1000]
                        projection["success_criteria"] = [c[:200] for c in goal.success_criteria]
                        projection["tasks"] = [{"id": t.id, "title": t.title[:200], "status": t.status}
                                               for t in goal.tasks[:10]]
                        projection["task_total"] = len(goal.tasks)
                return {"status": "ready", "goal": projection, "truncated": truncated,
                        "lookup": ("agentic.get_goal; read full objective, scope and criteria before acting"
                                   if truncated else "agentic.get_goal for full evidence and history") if goal else None,
                        "automatic_continuation": bool(goal and goal.status == "active" and self.enabled())}
            if method == "get":
                return deepcopy(self._goal(payload["goal_id"]))
            if method == "list":
                return {"goals": [{**{k: deepcopy(g[k]) for k in ("id", "status", "revision", "updated_at")},
                                   "objective": g.objective[:300]} for g in list(self.goals.values())[-50:]],
                        "total": len(self.goals), "truncated": len(self.goals) > 50}
            if method == "admit":
                goal = next((g for g in self.goals.values() if g.pending and g.pending["id"] == command_id), None)
                return {"allowed": bool(goal and goal.status == "active" and self.enabled())}
            records = deepcopy(self.goals)
            if method == "create":
                if any(g.status in {"active", "suspended"} for g in records.values()):
                    raise ValueError("an open goal already exists; resume or ask the user to cancel it")
                if set(payload) - {"objective", "scope", "success_criteria", "tasks"}:
                    raise ValueError("unsupported goal creation fields")
                task_titles = payload.get("tasks")
                if not isinstance(task_titles, list):
                    raise ValueError("tasks must be a list of title strings")
                goal = Goal(id=timestamp_id(), objective=text(payload.get("objective"), "objective"),
                            scope=text(payload.get("scope"), "scope"), success_criteria=criteria(payload.get("success_criteria")),
                            tasks=[GoalTask(id=timestamp_id(), title=text(t, "task title", 2000)) for t in task_titles],
                            created_at=now(), updated_at=now())
                records[goal.id] = goal
            else:
                if type(payload.get("expected_revision")) is not int:
                    raise ValueError("expected_revision must be an integer")
                original = self._goal(payload["goal_id"], payload["expected_revision"])
                if original.status in {"completed", "cancelled"}:
                    raise ValueError("closed goals cannot be modified")
                goal = records[original.id]
                if method == "progress":
                    changes = payload["changes"]
                    if not changes or set(changes) - {"summary", "tasks"}:
                        raise ValueError("progress accepts only summary and tasks")
                    if "summary" in changes:
                        goal.summary = text(changes["summary"], "summary")
                    if "tasks" in changes:
                        if not isinstance(changes["tasks"], list):
                            raise ValueError("tasks must be a list")
                        tasks = []
                        old_ids = {t.id for t in original.tasks}
                        for item in changes["tasks"]:
                            if not isinstance(item, dict) or set(item) - {"id", "title", "status", "evidence"}:
                                raise ValueError("invalid task fields")
                            if "id" in item and item["id"] not in old_ids:
                                raise ValueError("unknown task id; omit id for new tasks")
                            tasks.append(GoalTask(**{**item, "id": item.get("id") or timestamp_id()}))
                        if not old_ids <= {t.id for t in tasks}:
                            raise ValueError("preserve existing tasks; mark obsolete tasks skipped with a reason")
                        goal.tasks = tasks
                    if goal.summary != original.summary or goal.tasks != original.tasks:
                        goal.progress += 1
                elif method == "suspend":
                    goal.status, goal.outcome = "suspended", text(payload.get("reason"), "suspension reason")
                    goal.pending = None
                elif method == "complete":
                    if goal.status != "active":
                        raise ValueError("only an active goal can be completed")
                    goal.status, goal.outcome = "completed", text(payload.get("evidence"), "verification evidence")
                    goal.pending = None
                elif method == "revise":
                    instruction = text(payload.get("user_instruction"), "explicit human instruction")
                    changes = payload["changes"]
                    if not changes or set(changes) - {"objective", "scope", "success_criteria", "status"}:
                        raise ValueError("unsupported goal correction fields")
                    for field in ("objective", "scope"):
                        if field in changes:
                            goal[field] = text(changes[field], field)
                    if "success_criteria" in changes:
                        goal.success_criteria = criteria(changes["success_criteria"])
                    if "status" in changes:
                        if changes["status"] not in {"active", "cancelled"}:
                            raise ValueError("human revision status must be active or cancelled")
                        goal.status = changes["status"]
                    goal.user_instruction = instruction
                    goal.outcome = instruction if goal.status == "cancelled" else ""
                    goal.pending = None
                    goal.stalled_turns = goal.failures = 0
                    goal.progress += 1
                else:
                    raise ValueError(f"unsupported agentic method: {method}")
                goal.revision += 1
                goal.updated_at = now()
            validate_goal(goal)
            await self._commit(records)
            return deepcopy(goal)

    async def observe(self, output):
        if not isinstance(output, (CommandCompleted, CommandFailed)):
            return
        async with self.lock:
            if self.error is not None:
                return
            goal = next((g for g in self.goals.values() if g.pending and g.pending["id"] == output.command.id), None)
            if goal is None:
                return
            records = deepcopy(self.goals)
            goal = records[goal.id]
            if isinstance(output, CommandFailed):
                goal.failures += 1
                if goal.failures >= 3:
                    goal.status = "suspended"
                    goal.outcome = f"Three continuation commands failed; last error: {output.error}"
            else:
                goal.failures = 0
                goal.stalled_turns = goal.stalled_turns + 1 if goal.progress == goal.pending["progress"] else 0
                if goal.stalled_turns >= 3:
                    goal.status = "suspended"
                    goal.outcome = "Three continuation turns without a recorded progress checkpoint. Inspect work before resuming."
            goal.pending = None
            goal.revision += 1
            goal.updated_at = now()
            await self._commit(records)
            self.last_attempt = 0.0
            self.delivery_failures = 0

    async def dispatch(self):
        async with self.lock:
            if self.error is not None or not self.enabled():
                return
            goal = next((g for g in self.goals.values() if g.status == "active"), None)
            if goal is None:
                return
            current_time = asyncio.get_running_loop().time()
            if current_time - self.last_attempt < self.retry_interval:
                return
            if goal.pending is None:
                records = deepcopy(self.goals)
                goal = records[goal.id]
                goal.sequence += 1
                goal.pending = {"id": f"goal-{goal.id}-{goal.sequence}", "progress": goal.progress}
                await self._commit(records)
            command = PromptRequest(
                id=goal.pending["id"], priority=100, admission="agentic",
                prompt_role="developer", prompt_kind="goal_continuation",
                prompt=(f'<goal_continuation id="{escape(goal.id)}">'
                        'Continue the authorized current goal using the current_goal provider. '
                        'Inspect saved progress and actual effects before retrying work. '
                        'Organize coherent subtasks, record meaningful progress, verify completion, '
                        'or suspend with a reason when blocked. Never autonomously cancel or change '
                        'the objective. Respect newer human instructions and all existing permission boundaries.'
                        '</goal_continuation>'),
            )
            self.last_attempt = current_time
        try:
            await self.deliver(command)
            self.delivery_failures = 0
        except Exception as error:
            self.delivery_failures += 1
            if self.delivery_failures >= 3:
                async with self.lock:
                    current = self.goals.get(goal.id)
                    if current and current.status == "active" and current.pending and current.pending["id"] == command.id:
                        records = deepcopy(self.goals)
                        current = records[goal.id]
                        current.status, current.outcome = "suspended", f"Three delivery attempts failed: {error}"
                        current.pending = None
                        current.revision += 1
                        current.updated_at = now()
                        await self._commit(records)

    async def _run(self):
        while True:
            try:
                await self.dispatch()
            except Exception as error:
                self.error = f"agentic continuation unavailable: {error}"
            await asyncio.sleep(self.poll_interval)
