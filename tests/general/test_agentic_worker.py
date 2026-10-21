"""Queue admission is generic; the plugin owns goal lifecycle decisions."""

import asyncio

from the_framework.agent import Agent
from the_framework.agent.models.events import Event
from the_framework.agent.runtime.protocol import (
    ApplicationCall, ApplicationResult, CommandCompleted, PromptRequest, ShutdownRequest,
)
from the_framework.agent.runtime.worker import Worker
from the_framework.plugins.agentic import AgenticExtension
from the_framework.plugins.agentic.service import AgenticService


def test_worker_admission_rejects_stale_work_without_running_inference():
    async def test():
        agent = Agent(client=object())
        worker = Worker(agent, None, None)
        decisions = []
        async def admission(request):
            decisions.append(request)
            return {"allowed": False}
        agent.bridge.bind(admission)
        ran = []
        async def stream(prompt, **options):
            ran.append(prompt)
            yield Event.from_dict({"type": "agent.step.start"})
        agent.astream = stream
        command = PromptRequest(id="obsolete", prompt="do not execute", admission="example")
        assert [event async for event in worker.dispatch(command)] == []
        assert not ran
        assert decisions[0].capability == "example" and decisions[0].method == "admit"
        # Missing/malformed permission is denied, never truthiness-based approval.
        for decision in ({}, {"allowed": "yes"}, True):
            async def malformed(_request):
                return decision
            agent.bridge.bind(malformed)
            assert [event async for event in worker.dispatch(command)] == []
        async def allowed(_request):
            return {"allowed": True}
        agent.bridge.bind(allowed)
        assert len([event async for event in worker.dispatch(command)]) == 1
        assert ran == ["do not execute"]
    asyncio.run(test())


def test_lower_priority_or_guarded_prompts_cannot_steer_human_work():
    agent = Agent(client=object())
    worker = Worker(agent, None, None)
    agent.agentic_loop.can_steer = lambda: True
    human = PromptRequest(id="human", prompt="human", status="active")
    goal = PromptRequest(id="goal", prompt="goal", priority=100, admission="agentic", status="active")
    worker.commands.active = human
    assert not worker.can_steer(goal)
    assert not worker.can_steer(PromptRequest(id="background", prompt="background", priority=100))
    worker.commands.active = goal
    assert worker.can_steer(human)


def test_real_worker_goal_lifecycle_and_late_duplicate_after_restart(tmp_path):
    async def test():
        incoming = asyncio.Queue()
        outputs = []
        agent = Agent(client=object())
        extension = agent.add_extension(AgenticExtension)
        worker = None
        service = None
        finished = asyncio.Event()
        streams = []

        async def send(output):
            outputs.append(output)
            if isinstance(output, ApplicationCall):
                try:
                    result = await service.handle(output.request.method, output.request.payload,
                                                  command_id=output.command_id)
                    response = ApplicationResult(id=output.request.id, status="success", result=result)
                except Exception as error:
                    response = ApplicationResult(id=output.request.id, status="failure", error=str(error))
                await worker.handle(response)
            if isinstance(output, CommandCompleted):
                await service.observe(output)
                goal = next(iter(service.goals.values()))
                if goal.status == "completed":
                    finished.set()
                else:
                    await service.dispatch()

        async def stream(prompt, **options):
            streams.append(prompt)
            goal = (await extension.current_goal())["goal"]
            if len(streams) == 1:
                tasks = [{**t, "status": "completed", "evidence": "Integration verification succeeded"} for t in goal.tasks]
                await extension.update_goal_progress(goal.id, goal.revision, __import__("json").dumps({"tasks": tasks}))
            else:
                await extension.complete_goal(goal.id, goal.revision, "Two real canonical turns; progress and completion verified")
            yield Event.from_dict({"type": "agent.step.start"})

        agent.astream = stream
        worker = Worker(agent, incoming.get, send)
        service = AgenticService(tmp_path / "goals.json", incoming.put, retry_interval=0)
        goal = await service.handle("create", {"objective": "Test autonomous continuation", "scope": "Isolated test only",
                                               "success_criteria": ["Two turns then stop"], "tasks": ["Verify lifecycle"]})
        await service.dispatch()
        first_command = await incoming.get()
        await incoming.put(first_command)
        runner = asyncio.create_task(worker.run())
        try:
            await asyncio.wait_for(finished.wait(), 5)
            assert len(streams) == 2
            assert service.goals[goal.id].status == "completed"
            await worker.handle(first_command)
            assert len(streams) == 2
            await service.dispatch()
            assert incoming.empty()
            # A completed command receipt is returned for an old outbox id,
            # without replaying the effect even after an independent service load.
            recovered = AgenticService(service.path, incoming.put, poll_interval=100)
            await recovered.start()
            await recovered.stop()
            assert recovered.goals[goal.id].status == "completed"
        finally:
            await incoming.put(ShutdownRequest(id="stop"))
            await asyncio.wait_for(runner, 5)
    asyncio.run(test())


def test_owner_admission_runs_after_middleware_wait():
    async def run():
        agent = Agent(client=object())
        sequence = []

        async def middleware(agent, command, call_next):
            sequence.append("wait")
            async for event in call_next(command):
                yield event

        async def admission(request):
            sequence.append("admit")
            return {"allowed": False}

        worker = Worker(agent, None, None, command_middleware=(middleware,))
        agent.bridge.bind(admission)
        command = PromptRequest(id="delayed", prompt="do not execute", admission="owner")
        assert [event async for event in worker.dispatch(command)] == []
        assert sequence == ["wait", "admit"]
    asyncio.run(run())


def test_shutdown_preserves_deferred_work_and_recovery_executes_once(tmp_path):
    import json
    from the_framework.agent.context.session import Session

    async def run():
        path = tmp_path / "session.json"
        command = PromptRequest(id="held", prompt="preserved")
        session = Session.open(path)
        session.set_command(command.id, "waiting", deferred=json.loads(json.dumps(command)))
        incoming = asyncio.Queue()
        entered = asyncio.Event()
        async def hold(agent, command, call_next):
            command.status = "waiting"
            await asyncio.to_thread(agent.session.set_command, command.id, "waiting",
                                    deferred=json.loads(json.dumps(command)))
            entered.set()
            await incoming.put(ShutdownRequest(id="stop"))
            while command.status != "interrupted":
                await asyncio.sleep(.001)
            if False:
                yield
        async def send(output):
            pass
        first = Worker(Agent(client=object(), session=Session.open(path)), incoming.get, send,
                       command_middleware=(hold,))
        await asyncio.wait_for(first.run(), 3)
        assert entered.is_set()
        restored = Session.open(path)
        assert restored.commands["held"].deferred["prompt"] == "preserved"
        assert restored.command_status("held") == "waiting"
        incoming = asyncio.Queue()
        ran = []
        agent = Agent(client=object(), session=restored)
        async def stream(prompt, **options):
            # Clear replay intent BEFORE the first potential model effect.
            assert agent.session.commands["held"].deferred is None
            ran.append(prompt)
            yield Event.from_dict({"type": "agent.step.start"})
        async def completed(output):
            if isinstance(output, CommandCompleted):
                await incoming.put(ShutdownRequest(id="done"))
        agent.astream = stream
        worker = Worker(agent, incoming.get, completed)
        await asyncio.wait_for(worker.run(), 3)
        assert ran == ["preserved"]
        assert Session.open(path).command_status("held") == "completed"
    asyncio.run(run())
