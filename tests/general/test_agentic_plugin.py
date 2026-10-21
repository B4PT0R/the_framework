import asyncio
from copy import deepcopy

import pytest

from the_framework.agent import Agent
from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest
from the_framework.plugins.agentic import AGENTIC, AgenticExtension
from the_framework.plugins.agentic.service import AgenticService


SPEC = {"objective": "Implement feature", "scope": "Local code and tests; no push",
        "success_criteria": ["Tests pass", "Running application verified"],
        "tasks": ["Inspect", "Implement and verify"]}


def run(function):
    asyncio.run(function())


async def create(service):
    return await service.handle("create", deepcopy(SPEC))


async def change(service, goal, method, **payload):
    return await service.handle(method, {"goal_id": goal.id, "expected_revision": goal.revision, **payload})


def test_single_open_goal_and_suspended_slot(tmp_path):
    async def test():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await create(service)
        with pytest.raises(ValueError, match="open goal"):
            await create(service)
        goal = await change(service, goal, "suspend", reason="Missing permission; inspected alternatives; ask owner")
        assert goal.status == "suspended"
        with pytest.raises(ValueError, match="open goal"):
            await create(service)
        with pytest.raises(ValueError, match="human instruction"):
            await change(service, goal, "revise", changes={"status": "cancelled"}, user_instruction="")
        with pytest.raises(ValueError, match="unsupported agentic"):
            await change(service, goal, "cancel")
        goal = await change(service, goal, "revise", changes={"status": "cancelled"}, user_instruction="Cancel this goal")
        assert goal.status == "cancelled"
        assert (await create(service)).id != goal.id
    run(test)


def test_progress_is_evolving_but_cannot_replace_objective_or_drop_work(tmp_path):
    async def test():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await create(service)
        tasks = deepcopy(goal.tasks)
        tasks[0].status, tasks[0].evidence = "completed", "Read relevant files"
        tasks.append({"title": "Additional integration test"})
        goal = await change(service, goal, "progress", changes={"tasks": tasks, "summary": "Inspection finished"})
        assert len(goal.tasks) == 3 and goal.progress == 1
        with pytest.raises(ValueError, match="stale"):
            await service.handle("progress", {"goal_id": goal.id, "expected_revision": 1, "changes": {"summary": "obsolete"}})
        for changes in ({"objective": "different"}, {"tasks": []},
                        {"tasks": [{"id": "invented", "title": "wrong"}]}):
            with pytest.raises(ValueError):
                await change(service, goal, "progress", changes=changes)
        with pytest.raises(ValueError, match="every task"):
            await change(service, goal, "complete", evidence="Tests passed")
        assert service.goals[goal.id].status == "active"
        tasks = deepcopy(goal.tasks)
        for task in tasks:
            task.status, task.evidence = "completed", "Verified through targeted tests"
        goal = await change(service, goal, "progress", changes={"tasks": tasks})
        with pytest.raises(ValueError, match="verification evidence"):
            await change(service, goal, "complete", evidence="")
        goal = await change(service, goal, "complete", evidence="All criteria checked: tests and active application verified")
        assert goal.status == "completed"
        with pytest.raises(ValueError, match="closed"):
            await change(service, goal, "progress", changes={"summary": "too late"})
    run(test)


def test_continuation_outbox_persists_and_retries_same_id(tmp_path):
    async def test():
        delivered = []
        async def deliver(command):
            delivered.append(command)
        service = AgenticService(tmp_path / "goals.json", deliver, retry_interval=0)
        goal = await create(service)
        await service.dispatch()
        command = delivered[-1]
        assert command.priority > PromptRequest(id="human", prompt="hi").priority
        assert command.admission == "agentic" and command.prompt_role == "developer"
        assert (await service.handle("admit", {}, command_id=command.id))["allowed"]
        await service.dispatch()
        assert delivered[-1].id == command.id
        # Restart recovers the same outbox identity, not a second goal turn.
        restarted = AgenticService(service.path, deliver, retry_interval=0, poll_interval=100)
        await restarted.start()
        try:
            await restarted.dispatch()
            assert delivered[-1].id == command.id
            before = deepcopy(restarted.goals[goal.id])
            updated = await change(restarted, before, "progress", changes={"summary": "Meaningful checkpoint"})
            await restarted.observe(CommandCompleted(request_id=command.id, command=command))
            assert restarted.goals[goal.id].pending is None
            await restarted.dispatch()
            assert delivered[-1].id != command.id
            # Duplicate completion cannot consume the newer turn.
            newer = deepcopy(restarted.goals[goal.id].pending)
            await restarted.observe(CommandCompleted(request_id=command.id, command=command))
            assert restarted.goals[goal.id].pending == newer
        finally:
            await restarted.stop()
        assert service.path.stat().st_mode & 0o777 == 0o600
    run(test)


@pytest.mark.parametrize("transition", ["suspend", "complete", "revise"])
def test_queued_continuation_is_inert_after_transition(tmp_path, transition):
    async def test():
        delivered = []
        async def deliver(command):
            delivered.append(command)
        service = AgenticService(tmp_path / "goals.json", deliver, retry_interval=0)
        goal = await create(service)
        if transition == "complete":
            tasks = [{**t, "status": "completed", "evidence": "Verified"} for t in goal.tasks]
            goal = await change(service, goal, "progress", changes={"tasks": tasks})
        await service.dispatch()
        command = delivered[-1]
        goal = service.goals[goal.id]
        kwargs = {"reason": "Blocked"} if transition == "suspend" else {"evidence": "Verified criteria"}
        if transition == "revise":
            kwargs = {"changes": {"objective": "Corrected objective"}, "user_instruction": "Use this corrected objective"}
        goal = await change(service, goal, transition, **kwargs)
        assert not (await service.handle("admit", {}, command_id=command.id))["allowed"]
        await service.observe(CommandCompleted(request_id=command.id, command=command))
        if transition != "revise":
            count = len(delivered)
            await service.dispatch()
            assert len(delivered) == count
    run(test)


def test_stalled_turns_suspend_without_cancelling_and_can_resume(tmp_path):
    async def test():
        delivered = []
        async def deliver(command):
            delivered.append(command)
        service = AgenticService(tmp_path / "goals.json", deliver, retry_interval=0)
        goal = await create(service)
        for _ in range(3):
            await service.dispatch()
            command = delivered[-1]
            await service.observe(CommandCompleted(request_id=command.id, command=command))
        goal = service.goals[goal.id]
        assert goal.status == "suspended" and "progress" in goal.outcome
        assert goal.tasks[0].status == "pending"
        goal = await change(service, goal, "revise", changes={"status": "active"}, user_instruction="Resume now")
        assert goal.status == "active" and goal.stalled_turns == 0
        await service.dispatch()
        assert len(delivered) == 4
    run(test)


def test_unchanged_progress_does_not_defeat_stall_detection(tmp_path):
    async def test():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await create(service)
        goal = await change(service, goal, "progress", changes={"summary": "Waiting"})
        goal = await change(service, goal, "progress", changes={"summary": "Waiting"})
        assert goal.progress == 1
    run(test)


@pytest.mark.parametrize("failure", ["command", "delivery"])
def test_failures_are_bounded_and_suspend(tmp_path, failure):
    async def test():
        delivered = []
        async def deliver(command):
            delivered.append(command)
            if failure == "delivery":
                raise RuntimeError("Transport unavailable")
        service = AgenticService(tmp_path / "goals.json", deliver, retry_interval=0)
        goal = await create(service)
        for _ in range(3):
            await service.dispatch()
            command = delivered[-1]
            if failure == "command":
                await service.observe(CommandFailed(request_id=command.id, command=command, error="backend unavailable"))
        assert service.goals[goal.id].status == "suspended"
        count = len(delivered)
        await service.dispatch()
        assert len(delivered) == count
    run(test)


def test_disabled_binding_stops_dispatch_and_admission(tmp_path):
    async def test():
        enabled = True
        delivered = []
        async def deliver(command):
            delivered.append(command)
        service = AgenticService(tmp_path / "goals.json", deliver, enabled=lambda: enabled, retry_interval=0)
        await create(service)
        await service.dispatch()
        enabled = False
        await service.dispatch()
        assert len(delivered) == 1
        assert not (await service.handle("admit", {}, command_id=delivered[0].id))["allowed"]
        assert not (await service.handle("current", {}))["automatic_continuation"]
    run(test)


def test_corrupt_storage_and_failed_persistence_fail_closed(tmp_path, monkeypatch):
    async def test():
        path = tmp_path / "goals.json"
        path.write_text("broken")
        service = AgenticService(path, None)
        await service.start()
        assert service.error and service.task is None
        with pytest.raises(RuntimeError):
            await create(service)
        assert path.read_text() == "broken"
        path.unlink()
        await service.start()
        try:
            goal = await create(service)
            def fail(_records):
                raise OSError("disk full")
            monkeypatch.setattr(service.store, "save", fail)
            with pytest.raises(OSError):
                await change(service, goal, "suspend", reason="blocked")
            assert service.goals[goal.id].status == "active" and service.error
        finally:
            await service.stop()
    run(test)


def test_extension_bridge_provider_and_surface_ownership():
    async def test():
        agent = Agent(client=object())
        extension = agent.add_extension(AgenticExtension)
        calls = []
        async def request(payload):
            calls.append(payload)
            return {"status": "ready", "goal": None}
        agent.bridge.bind(request)
        assert await extension.current_goal() == {"status": "ready", "goal": None}
        await extension.create_goal(__import__("json").dumps(SPEC))
        assert calls[-1].capability == "agentic" and calls[-1].method == "create"
        namespace = agent.registry("tools")["agentic"]
        assert all(namespace.find(name) is not None for name in ("wait", "end_turn", "create_goal", "suspend_goal"))
        assert AGENTIC.server_extensions[0].scope == "session"
        from the_framework.plugins.system import SystemExtension
        system = agent.add_extension(SystemExtension)
        assert not hasattr(system, "wait") and not hasattr(system, "end_turn")
        async def fail(_payload):
            raise RuntimeError("offline")
        agent.bridge.bind(fail)
        assert (await extension.current_goal())["status"] == "unavailable"
    run(test)


@pytest.mark.parametrize("revision", [None, True, "1", 1.0])
def test_mutations_require_an_actual_integer_revision(tmp_path, revision):
    # Use the bridge payload shape directly, not a model that might coerce a value.
    async def direct():
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await create(service)
        with pytest.raises(ValueError, match="integer"):
            await service.handle("suspend", {"goal_id": goal.id, "expected_revision": revision, "reason": "Blocked"})
        assert service.goals[goal.id].status == "active"
    run(direct)


def test_live_projection_and_history_listing_are_bounded(tmp_path):
    async def test():
        import json
        service = AgenticService(tmp_path / "goals.json", None)
        goal = await service.handle("create", {**deepcopy(SPEC), "objective": "x" * 16000,
                                                "tasks": ["Work " + str(i) + "x" * 1900 for i in range(100)]})
        projection = await service.handle("current", {})
        assert projection["truncated"] and "get_goal" in projection["lookup"]
        assert len(json.dumps(projection)) < 20000
        listing = await service.handle("list", {})
        assert len(listing["goals"][0]["objective"]) == 300
        assert len((await service.handle("get", {"goal_id": goal.id})).tasks) == 100
    run(test)
