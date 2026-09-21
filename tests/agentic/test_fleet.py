import asyncio
import json
from typing import ClassVar

import pytest

from core.agent.models.worker import WorkerProfile
from core.agent.models.content import OutputText
from core.agent.models.events import Event, ResponseOutputItemDone
from core.agent.models.lifecycle import (
    AgentTaskRequested,
    AgentCompactionEnd,
    AgentResponseItemAdded,
    AgentStepStart,
    AgentTurnEnd,
)
from core.agent.runtime.protocol import CommandCompleted, CommandEvent, CommandFailed, WorkerReady
from core.agent.models.responses import (
    CompactionSummary,
    FunctionCall,
    FunctionCallOutput,
    Message,
)
from core.agent.context.session import SessionWatermark
from core.server.runtime.fleet import FleetSupervisor, agent_request_prompt


def test_specialist_request_envelope_preserves_arbitrary_task_and_provenance():
    event = AgentTaskRequested(
        request_id="task", profile="analysis", prompt="Compare these two designs.",
        source_item_ids=["design-a", "design-b"],
    )
    prompt = agent_request_prompt(event)
    body = prompt.split("<canonical_agent_request>\n", 1)[1].split(
        "\n</canonical_agent_request>", 1
    )[0]
    assert json.loads(body) == {
        "request": event.prompt,
        "source_item_ids": ["design-a", "design-b"],
    }


@pytest.mark.parametrize("reserved", ["model", "instructions"])
def test_auxiliary_profile_keeps_model_and_instructions_out_of_agent_config(reserved):
    with pytest.raises(ValueError, match="duplicates profile fields"):
        WorkerProfile(
            name="worker",
            instructions="Work.",
            agent_config={reserved: "duplicate"},
        )


class FakeSupervisor:
    instances: ClassVar[list] = []

    def __init__(self, session_path, command=None):
        self.session_path = session_path
        self.command = command
        self.outputs = asyncio.Queue()
        self.running = False
        self.ready = WorkerReady(session_id=f"session-{len(self.instances)}")
        self.sent = []
        self.instances.append(self)

    async def start(self):
        self.running = True
        return self.ready

    async def restart(self):
        return await self.start()

    async def send(self, request):
        self.sent.append(request)
        message = Message(
            role="assistant",
            content=[OutputText(text=f"result:{request.prompt[-20:]}")],
        )
        await self.outputs.put(CommandEvent(
            command_id=request.id,
            event=ResponseOutputItemDone(
                item=message,
                output_index=0,
                sequence_number=1,
            ),
            watermark=SessionWatermark(
                session_id=self.ready.session_id,
                revision=1,
                anchor_id=None,
                tail_start=0,
            ),
        ))
        await self.outputs.put(CommandCompleted(
            request_id=request.id,
            command=request,
        ))

    async def stop(self):
        self.running = False


class InterruptedSupervisor(FakeSupervisor):
    async def send(self, request):
        self.sent.append(request)
        await self.outputs.put(CommandFailed(
            request_id=request.id,
            command=request,
            error="interrupted",
        ))


class CompletionSupervisor(FakeSupervisor):
    async def send(self, request):
        self.sent.append(request)
        watermark = SessionWatermark(
            session_id=self.ready.session_id,
            revision=len(self.sent),
            anchor_id=None,
            tail_start=0,
        )
        if len(self.sent) == 1:
            items = [Message(
                role="assistant",
                content=[OutputText(text="Premature completion")],
            )]
        else:
            call = FunctionCall(
                name="complete_curation",
                arguments='{"coverage_summary":"Reviewed all subjects"}',
                call_id="complete-call",
            )
            items = [
                call,
                FunctionCallOutput(
                    call_id=call.call_id,
                    output=json.dumps({"status": "success", "message_ids": []}),
                ),
                Message(
                    role="assistant",
                    content=[OutputText(text="Verified completion")],
                ),
            ]
        for sequence_number, item in enumerate(items, 1):
            await self.outputs.put(CommandEvent(
                command_id=request.id,
                event=ResponseOutputItemDone(
                    item=item,
                    output_index=sequence_number - 1,
                    sequence_number=sequence_number,
                ),
                watermark=watermark,
            ))
        await self.outputs.put(CommandCompleted(
            request_id=request.id,
            command=request,
        ))


def profile(name="jiminy", **updates):
    payload = {
        "name": name,
        "model": "gpt-5.6-luna",
        "instructions": "Manage relational memory.",
        "observes": ["agent.response_item.added"],
    }
    payload.update(updates)
    return WorkerProfile(**payload)


def canonical_event(event_type="agent.response_item.added"):
    event = AgentResponseItemAdded(
        item=Message(role="user", content=[]),
    )
    if event_type != event.type:
        assert event_type == "agent.step.start"
        event = AgentStepStart()
    return CommandEvent(
        command_id="canonical-turn",
        event=event,
        watermark=SessionWatermark(
            session_id="canonical",
            revision=42,
            anchor_id="anchor",
            tail_start=1,
        ),
    )


def deliberate_auxiliary_request():
    return CommandEvent(
        command_id="canonical-turn",
        event=AgentTaskRequested(
            request_id="remember-python",
            profile="jiminy",
            prompt="Preserve the nuance of Baptiste's preference for Python.",
            source_item_ids=["user-message"],
        ),
        watermark=SessionWatermark(
            session_id="canonical",
            revision=42,
            anchor_id="anchor",
            tail_start=1,
        ),
    )


def test_deliberate_auxiliary_request_survives_protocol_serialization():
    event = deliberate_auxiliary_request().event

    restored = Event.from_dict(event.to_dict())

    assert isinstance(restored, AgentTaskRequested)
    assert restored.request_id == "remember-python"
    assert restored.source_item_ids == ["user-message"]


def test_canonical_message_survives_protocol_serialization_as_a_message():
    event = canonical_event().event

    restored = Event.from_dict(json.loads(json.dumps(event)))

    assert isinstance(restored, AgentResponseItemAdded)
    assert isinstance(restored.item, Message)
    assert restored.item.type == "message"
    assert restored.item.role == "user"


def test_fleet_runs_profiles_in_distinct_supervised_processes(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile("jiminy"), profile("observer")],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()

        submitted = await fleet.observe(canonical_event())
        first = await asyncio.wait_for(fleet.results.get(), 1)
        second = await asyncio.wait_for(fleet.results.get(), 1)

        assert submitted == 2
        assert {first.profile, second.profile} == {"jiminy", "observer"}
        assert first.watermark.session_id == "canonical"
        assert first.status == "completed"
        assert first.output.startswith("result:")
        assert first.metadata["observed_item_ids"]
        assert len(FakeSupervisor.instances) == 2
        assert FakeSupervisor.instances[0].session_path != FakeSupervisor.instances[1].session_path
        assert all(instance.running for instance in FakeSupervisor.instances)

        await fleet.stop()
        assert not any(instance.running for instance in FakeSupervisor.instances)

    asyncio.run(test())


def test_fleet_ignores_unobserved_events(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile()],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()

        assert await fleet.observe(canonical_event("agent.step.start")) == 0
        assert fleet.runners["jiminy"].queue.empty()

        await fleet.stop()

    asyncio.run(test())


def test_idle_auxiliary_starts_on_demand_and_releases_its_process(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(idle_timeout_seconds=0.01)],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()
        runner = fleet.runners["jiminy"]

        assert runner.running is False
        assert await fleet.observe(canonical_event()) == 1
        await asyncio.wait_for(fleet.results.get(), 1)
        assert runner.running is True

        await asyncio.sleep(0.02)
        assert runner.running is False
        assert runner.status()["active_task_id"] is None
        await fleet.stop()

    asyncio.run(test())


def test_fleet_routes_a_deliberate_request_only_to_its_named_auxiliary(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        observes = ["agent.task.requested"]
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [
                profile("jiminy", observes=observes, input_item_field="retired_items"),
                profile("observer", observes=observes),
            ],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()

        assert await fleet.observe(deliberate_auxiliary_request()) == 1
        result = await asyncio.wait_for(fleet.results.get(), 1)
        request = fleet.runners["jiminy"].supervisor.sent[0]

        assert result.profile == "jiminy"
        assert request.input_items == []
        assert "canonical_agent_request" in request.prompt
        assert "preference for Python" in request.prompt
        assert "user-message" in request.prompt
        assert result.metadata["agent_request_id"] == "remember-python"
        assert result.metadata["source_item_ids"] == ["user-message"]
        assert fleet.runners["observer"].supervisor.sent == []

        await fleet.stop()

    asyncio.run(test())


def test_fleet_filters_committed_items_by_local_kind(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(item_kinds=["realtime"])],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()

        assert await fleet.observe(canonical_event()) == 0
        event = canonical_event()
        event.event.item.kind = "realtime"
        assert await fleet.observe(event) == 1

        await fleet.stop()

    asyncio.run(test())


def test_latest_queue_policy_drops_the_oldest_pending_observation(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(queue_limit=1, queue_policy="latest")],
            supervisor_factory=FakeSupervisor,
        )
        runner = fleet.runners["jiminy"]
        first = canonical_event()
        second = canonical_event()

        await fleet.observe(first)
        await fleet.observe(second)

        assert runner.queue.qsize() == 1
        assert runner.dropped == 1

    asyncio.run(test())


def test_pending_auxiliary_tasks_survive_fleet_reconstruction(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        path = tmp_path / "session.json"
        first = FleetSupervisor(path, [profile()], supervisor_factory=FakeSupervisor)
        event = canonical_event()

        assert await first.observe(event) == 1
        task_id = first.runners["jiminy"].pending[0].id

        restored = FleetSupervisor(path, [profile()], supervisor_factory=FakeSupervisor)

        assert restored.runners["jiminy"].queue.qsize() == 1
        assert restored.runners["jiminy"].pending[0].id == task_id

    asyncio.run(test())


def test_stateless_nondurable_auxiliary_does_not_persist_pending_tasks(tmp_path):
    async def test():
        path = tmp_path / "session.json"
        fleet = FleetSupervisor(
            path,
            [profile(name="video_eyes_1", session_mode="ephemeral", durable_tasks=False)],
            supervisor_factory=FakeSupervisor,
        )

        assert await fleet.observe(canonical_event()) == 1
        runner = fleet.runners["video_eyes_1"]
        assert runner.pending
        assert not runner.tasks_path.exists()

        restored = FleetSupervisor(
            path,
            [profile(name="video_eyes_1", session_mode="ephemeral", durable_tasks=False)],
            supervisor_factory=FakeSupervisor,
        )
        assert restored.runners["video_eyes_1"].pending == []

    asyncio.run(test())


def test_interrupted_auxiliary_task_is_retired_instead_of_retried_forever(tmp_path):
    async def test():
        InterruptedSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile()],
            supervisor_factory=InterruptedSupervisor,
        )
        await fleet.start()

        await fleet.observe(canonical_event())
        result = await asyncio.wait_for(fleet.results.get(), 1)
        while fleet.runners["jiminy"].pending:
            await asyncio.sleep(0)

        assert result.status == "failed"
        assert result.error == "interrupted"
        assert fleet.runners["jiminy"].pending == []
        document = json.loads(fleet.runners["jiminy"].tasks_path.read_text())
        assert document["pending"] == []
        assert [value["id"] for value in document["outbox"]] == [result.id]
        assert await fleet.acknowledge(result) is True
        assert json.loads(fleet.runners["jiminy"].tasks_path.read_text()) == {
            "pending": [], "outbox": [],
        }
        await fleet.stop()

    asyncio.run(test())


def test_terminal_result_outbox_replays_and_acknowledges_idempotently(tmp_path):
    async def test():
        path = tmp_path / "session.json"
        first = FleetSupervisor(path, [profile()], supervisor_factory=FakeSupervisor)
        await first.start()
        await first.observe(canonical_event())
        result = await asyncio.wait_for(first.results.get(), 1)
        await first.stop()

        restored = FleetSupervisor(path, [profile()], supervisor_factory=FakeSupervisor)
        await restored.start()
        replayed = await asyncio.wait_for(restored.results.get(), 1)
        assert replayed.id == result.id
        assert await restored.acknowledge(replayed) is True
        assert await restored.acknowledge(replayed) is False
        await restored.stop()

    asyncio.run(test())


def test_auxiliary_requires_successful_completion_tool_before_finishing(tmp_path):
    async def test():
        CompletionSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(
                session_mode="ephemeral",
                completion_tool="complete_curation",
                completion_retry_limit=2,
            )],
            supervisor_factory=CompletionSupervisor,
        )
        await fleet.start()

        assert await fleet.observe(canonical_event()) == 1
        result = await asyncio.wait_for(fleet.results.get(), 1)
        requests = fleet.runners["jiminy"].supervisor.sent

        assert result.status == "completed"
        assert result.output == "Verified completion"
        assert len(requests) == 2
        assert requests[0].id != requests[1].id
        await fleet.stop()

    asyncio.run(test())


def test_auxiliary_completion_retries_are_bounded(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(
                session_mode="ephemeral",
                durable_tasks=False,
                completion_tool="complete_curation",
                completion_retry_limit=1,
            )],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()

        assert await fleet.observe(canonical_event()) == 1
        result = await asyncio.wait_for(fleet.results.get(), 1)

        assert result.status == "failed"
        assert "complete_curation" in result.error
        assert len(fleet.runners["jiminy"].supervisor.sent) == 2
        await fleet.stop()

    asyncio.run(test())


def test_conversation_observation_contains_only_selected_canonical_roles(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(observes=["agent.turn.end"], conversation_roles=["user", "assistant"])],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()
        user = canonical_event()
        developer = canonical_event()
        developer.event.item.role = "developer"
        assistant = canonical_event()
        assistant.event.item.role = "assistant"
        turn_end = CommandEvent(
            command_id="canonical-turn",
            event=AgentTurnEnd(),
            watermark=user.watermark,
        )

        await fleet.observe(user)
        await fleet.observe(developer)
        await fleet.observe(assistant)
        await fleet.observe(turn_end)
        await asyncio.wait_for(fleet.results.get(), 1)
        prompt = FakeSupervisor.instances[0].sent[0].prompt

        assert '"role":"user"' in prompt
        assert '"role":"assistant"' in prompt
        assert '"role":"developer"' not in prompt
        assert '"command_events"' not in prompt
        await fleet.stop()

    asyncio.run(test())


def test_compaction_observer_skips_the_first_anchor_and_passes_only_the_retired_item(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(
                observes=["agent.compaction.end"],
                input_item_field="retired_items",
            )],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()
        watermark = canonical_event().watermark
        first = CommandEvent(
            command_id="turn",
            event=AgentCompactionEnd(
                response={},
                retired_anchor_id=None,
                retired_items=[],
            ),
            watermark=watermark,
        )
        retired = CompactionSummary(encrypted_content="retired-secret")
        second = CommandEvent(
            command_id="turn",
            event=AgentCompactionEnd(
                response={},
                retired_anchor_id="summary-1",
                retired_items=[retired],
            ),
            watermark=watermark,
        )

        assert await fleet.observe(first) == 0
        assert await fleet.observe(second) == 1
        await asyncio.wait_for(fleet.results.get(), 1)
        request = FakeSupervisor.instances[0].sent[0]

        assert request.input_items == [retired]
        assert "retired-secret" not in request.prompt
        assert '"source_id":"summary-1"' in request.prompt
        assert "retired_anchor_id" not in request.prompt
        assert '"anchor_id"' not in request.prompt
        await fleet.stop()

    asyncio.run(test())


def test_turn_end_observation_contains_accumulated_command_events(tmp_path):
    async def test():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(observes=["agent.turn.end"])],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()
        item = canonical_event()
        turn_end = CommandEvent(
            command_id="canonical-turn",
            event=AgentTurnEnd(),
            watermark=item.watermark,
        )

        assert await fleet.observe(item) == 0
        assert await fleet.observe(turn_end) == 1
        result = await asyncio.wait_for(fleet.results.get(), 1)
        prompt = FakeSupervisor.instances[0].sent[0].prompt

        assert result.status == "completed"
        assert "agent.response_item.added" in prompt
        assert "agent.turn.end" in prompt
        assert result.metadata["observed_item_ids"]
        assert "canonical-turn" not in fleet.command_events

        await fleet.stop()

    asyncio.run(test())


def test_plugin_runtime_transition_stops_and_restarts_owned_agents(tmp_path):
    async def scenario():
        FakeSupervisor.instances = []
        fleet = FleetSupervisor(
            tmp_path / "session.json",
            [profile(name="memory.curator"), profile(name="webcam.observer")],
            supervisor_factory=FakeSupervisor,
        )
        await fleet.start()
        memory = fleet.runners["memory.curator"]
        webcam = fleet.runners["webcam.observer"]
        assert memory.running and webcam.running

        assert await fleet.set_plugin_running("memory", False) == (
            "memory.curator",
        )
        assert not memory.running
        assert webcam.running
        assert not await fleet.submit(
            "memory.curator",
            prompt="ignored while stopped",
            watermark=canonical_event().watermark,
            observation_type="manual",
        )

        await fleet.set_plugin_running("memory", True)
        assert memory.running
        assert "memory.curator" in fleet.enabled
        await fleet.stop()

    asyncio.run(scenario())
