from the_framework.agent.spec import AgentSpec
import asyncio
from types import SimpleNamespace

from the_framework.agent import Agent as RuntimeAgent
from the_framework.agent.runtime.application import Application
from the_framework.agent.extensions.specialists import AgentTriggers, agent_trigger
from the_framework.agent.runtime.event_loop import EventLoop
from the_framework.agent.models.events import Event
from the_framework.agent.extensions.hooks import Hooks
from the_framework.agent.models.lifecycle import AgentCompactionEnd, AgentCompactionStart
from the_framework.agent.context.projections import SessionProjections
from the_framework.agent.runtime.protocol import (
    ApplicationCall,
    ApplicationResult,
    CompactRequest,
    ConfigSnapshotRequest,
    ExternalEventRequest,
    PromptRequest,
    SessionPageRequest,
    SessionSnapshotRequest,
    ShutdownRequest,
    StatusRequest,
    TransientEventRequest,
)
from the_framework.agent.models.responses import FunctionCall, FunctionCallOutput, Message
from the_framework.agent.context.session import Session
from the_framework.agent.runtime.worker import Worker
from the_framework.plugins.system import SystemPlugin


class Agent:
    def __init__(self):
        self.session = Session(id="session")
        self.name = "test-agent"
        self.description = "Agent used by worker development tests."
        self.agents = AgentTriggers()
        self.application = Application()
        self.events = EventLoop(self)
        self.prompts = []
        self.prompt_options = []
        self.context = SimpleNamespace(last_local_input_tokens=123)
        self.configs = SimpleNamespace(
            compaction=SimpleNamespace(context_token_limit=200),
        )

    @property
    def id(self):
        return self.session.id

    async def astream(self, prompt, **options):
        self.prompts.append(prompt)
        self.prompt_options.append(options)
        yield Event.from_dict({"type": "unknown"})

    def interrupt(self, reason=None):
        pass

    def registry(self, name):
        return getattr(self, name)

    def add_response_item(self, item):
        self.session.append(item)
        return item


def test_worker_accepts_streams_and_completes_a_prompt():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        worker = Worker(Agent(), incoming.get, outgoing.append)
        send = worker.send

        async def async_send(message):
            send(message)

        worker.send = async_send
        await incoming.put(PromptRequest(id="one", prompt="hello"))
        await incoming.put(StatusRequest(id="two"))
        await incoming.put(ShutdownRequest(id="three"))

        await worker.run()

        types = [message.type for message in outgoing]
        assert types[0] == "worker_ready"
        assert outgoing[0].session_id == "session"
        assert outgoing[0].name == "test-agent"
        assert outgoing[0].description == "Agent used by worker development tests."
        assert types[1] == "command_accepted"
        assert outgoing[1].command.id == "one"
        status = next(message for message in outgoing if message.type == "worker_status")
        assert status.local_input_tokens == 123
        assert status.context_token_limit == 200
        assert status.context_saturation == 0.615
        assert "command_event" in types
        event = next(message for message in outgoing if message.type == "command_event")
        assert event.request_id == "one"
        assert event.command_id == "one"
        assert "command_completed" in types
        assert types[-1] == "worker_stopped"

    asyncio.run(test())


def test_worker_applies_a_prompt_reasoning_override_without_changing_defaults():
    async def test():
        agent = Agent()
        worker = Worker(agent, asyncio.Queue().get, lambda _message: None)

        events = [event async for event in worker.execute(PromptRequest(
            id="realtime-delegation",
            prompt="Réponds vite",
            reasoning_effort="none",
        ))]

        assert events
        assert agent.prompt_options == [{"reasoning_effort": "none"}]

    asyncio.run(test())


def test_worker_status_separates_internal_maintenance_from_foreground_work():
    async def test():
        outgoing = []

        async def send(message):
            outgoing.append(message)

        worker = Worker(Agent(), asyncio.Queue().get, send)
        worker.commands.active = ExternalEventRequest(
            id="maintenance",
            name="agent_result",
            status="active",
        )
        foreground = PromptRequest(id="conversation", prompt="hello")
        worker.live[foreground.id] = foreground

        await worker.handle(StatusRequest(id="status"))

        status = outgoing[-1]
        assert status.active.id == "maintenance"
        assert status.foreground_active is None
        assert status.foreground_pending == 1

    asyncio.run(test())


def test_busy_prompt_is_steered_into_the_active_turn_at_the_next_checkpoint():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = RuntimeAgent(client=object())
        started = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def step():
            seen.append([
                item.content[0].text
                for item in agent.session.history
                if isinstance(item, Message) and item.role == "user" and item.content
            ])
            if len(seen) == 1:
                started.set()
                await release.wait()
                agent.agentic_loop.checkpoint()
            else:
                agent.agentic_loop.request_next_step = False

        agent.agentic_loop.step = step

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        await incoming.put(PromptRequest(id="turn", prompt="initial"))
        await started.wait()
        await incoming.put(PromptRequest(id="steer", prompt="change direction"))
        while not any(
            output.type == "command_accepted" and output.request_id == "steer"
            for output in outgoing
        ):
            await asyncio.sleep(0)
        release.set()
        while len([
            output for output in outgoing if output.type == "command_completed"
        ]) < 2:
            await asyncio.sleep(0)
        await incoming.put(ShutdownRequest(id="stop"))
        await running

        assert seen == [["initial"], ["initial", "change direction"]]
        assert agent.session.command_status("steer") == "completed"
        assert agent.session.command_status("turn") == "completed"

    asyncio.run(test())


def test_external_prompt_wakes_system_wait_and_resumes_the_active_turn():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = RuntimeAgent(
            client=object(),
            system={"default_wait_seconds": 1, "max_wait_seconds": 3},
        )
        system = agent.add_plugin(SystemPlugin)
        waiting = asyncio.Event()
        wait_results = []
        seen = []

        async def step():
            seen.append([
                item.content[0].text
                for item in agent.session.history
                if isinstance(item, Message) and item.role == "user" and item.content
            ])
            if len(seen) == 1:
                waiting.set()
                wait_results.append(await asyncio.to_thread(system.wait, 3))
                agent.agentic_loop.checkpoint()
            else:
                agent.agentic_loop.request_next_step = False

        agent.agentic_loop.step = step

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        await incoming.put(PromptRequest(id="turn", prompt="initial"))
        await waiting.wait()
        await incoming.put(PromptRequest(id="steer", prompt="wake now"))

        while len([
            output for output in outgoing if output.type == "command_completed"
        ]) < 2:
            await asyncio.sleep(0)
        await incoming.put(ShutdownRequest(id="stop"))
        await running

        assert wait_results[0]["status"] == "steering"
        assert wait_results[0]["waited_seconds"] < 1
        assert seen == [["initial"], ["initial", "wake now"]]
        assert agent.session.command_status("steer") == "completed"

    asyncio.run(test())


def test_manual_compaction_runs_as_a_sequential_worker_command():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()
        compacted = []
        measured = []
        def compact():
            compacted.append(True)
            agent.events.emit(AgentCompactionStart())
            agent.events.emit(AgentCompactionEnd(response={}))
        agent.compact = compact
        async def generation_payload():
            return {"input": []}
        agent.agentic_loop = SimpleNamespace(ageneration_payload=generation_payload)
        agent.context.local_input_tokens = lambda payload: measured.append(payload) or 0

        async def send(message):
            outgoing.append(message)

        await incoming.put(CompactRequest(id="compact"))
        await incoming.put(ShutdownRequest(id="stop"))
        await Worker(agent, incoming.get, send).run()

        assert compacted == [True]
        assert measured == [{"input": []}]
        events = [
            value.event.type for value in outgoing
            if value.type == "command_event"
        ]
        assert events == ["agent.compaction.start", "agent.compaction.end"]
        completed = next(value for value in outgoing if value.type == "command_completed")
        assert completed.request_id == "compact"

    asyncio.run(test())


def test_worker_maps_authenticated_usage_windows_into_status():
    async def test():
        agent = Agent()
        agent.client = SimpleNamespace(codex=SimpleNamespace(usage=lambda: {
            "rate_limit": {
                "primary_window": {
                    "used_percent": 21,
                    "limit_window_seconds": 5 * 60 * 60,
                    "reset_at": 1_800_000_000,
                },
                "secondary_window": {
                    "used_percent": 43,
                    "limit_window_seconds": 7 * 24 * 60 * 60,
                    "reset_at": 1_800_001_000,
                },
            },
        }))
        worker = Worker(agent, asyncio.Queue().get, lambda value: None)

        await worker.refresh_quota()

        assert worker.quota["quota_5h"].used_percent == 21
        assert worker.quota["quota_7d"].used_percent == 43
        assert worker.quota["quota_updated_at"] is not None

    asyncio.run(test())


def test_non_retained_auxiliary_context_is_cleared_after_each_prompt():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()
        agent.retain_history = False

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, incoming.get, send)
        await incoming.put(PromptRequest(id="task", prompt="ephemeral observation"))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        assert agent.prompts == ["ephemeral observation"]
        assert agent.session.history == []
        assert agent.session.archive == []

    asyncio.run(test())


def test_prompt_input_items_enter_auxiliary_context_before_the_prompt():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()
        seen = []
        original = agent.astream

        async def astream(prompt):
            seen.extend(agent.session.history)
            async for event in original(prompt):
                yield event

        agent.astream = astream
        retired = Message(role="developer", content=[])

        async def send(message):
            outgoing.append(message)

        await incoming.put(PromptRequest(
            id="task",
            prompt="curate",
            input_items=[retired],
        ))
        await incoming.put(ShutdownRequest(id="stop"))

        await Worker(agent, incoming.get, send).run()

        assert seen == [retired]

    asyncio.run(test())


def test_worker_ready_declares_auxiliary_profiles_and_results_are_dispatched():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()
        results = []

        @agent_trigger(
            AgentSpec(
                name="jiminy",
                instructions="Remember important facts.",
                configuration={"model": "gpt-5.6-luna"},
            ),
            observes=["agent.response_item.added"],
        )
        def jiminy(result):
            results.append(result)

        agent.agents.add(jiminy)

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, incoming.get, send)
        await incoming.put(ExternalEventRequest(
            id="result",
            name="agent_result",
            payload={"profile": "jiminy", "status": "completed"},
        ))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        ready = outgoing[0]
        assert ready.specialists[0].name == "jiminy"
        assert ready.specialists[0].model == "gpt-5.6-luna"
        assert results == [{"profile": "jiminy", "status": "completed"}]

    asyncio.run(test())


def test_worker_snapshot_separates_active_context_from_local_archive():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = Agent()
        archived = Message(role="user", content=[])
        anchor = Message(role="assistant", content=[])
        agent.session.archive.append(archived)
        agent.session.history.append(anchor)
        hooks = type("Registry", (), {
            "run": lambda self, hook, snapshot: snapshot,
        })()
        agent.registry = lambda name: hooks if name == "hooks" else agent.agents
        agent.context = type("Context", (), {"instructions": lambda self: ""})()
        worker = Worker(agent, incoming.get, send)
        await incoming.put(SessionSnapshotRequest(id="snapshot"))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        snapshot = next(output for output in outgoing if output.type == "session_snapshot")
        assert [item.id for item in snapshot["items"]] == [anchor.id]
        assert [item.id for item in snapshot.archive] == [archived.id]

    asyncio.run(test())


def test_worker_pages_archive_without_splitting_turns():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = Agent()
        for index in range(3):
            agent.session.archive.extend([
                Message(id=f"user-{index}", role="user", content=[]),
                Message(id=f"assistant-{index}", role="assistant", content=[]),
            ])
        worker = Worker(agent, incoming.get, send)
        await incoming.put(SessionPageRequest(id="page", limit=2))
        await incoming.put(SessionPageRequest(id="older", before="user-1", limit=2))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        page = next(output for output in outgoing if output.type == "session_page")
        assert [turn.id for turn in page.turns] == ["user-1", "user-2"]
        assert [[item.id for item in turn["items"]] for turn in page.turns] == [
            ["user-1", "assistant-1"],
            ["user-2", "assistant-2"],
        ]
        assert page.next_before == "user-1"
        assert page.has_more is True

        older = next(
            output for output in outgoing
            if output.type == "session_page" and output.request_id == "older"
        )
        assert [turn.id for turn in older.turns] == ["user-0"]
        assert [item.id for item in older.turns[0]["items"]] == [
            "user-0",
            "assistant-0",
        ]
        assert older.next_before is None
        assert older.has_more is False

    asyncio.run(test())


def test_worker_projects_only_displayable_session_items(tmp_path):
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = Agent()
        from the_framework.agent.models.responses import Image
        image_path = tmp_path / "image.png"
        image_path.write_bytes(b"image")

        agent.session.archive.extend([
            Message(id="user", role="user", content=[]),
            Message(id="tool", role="developer", kind="tool_output", content=[]),
            FunctionCall(id="call", name="tool", arguments="{}", call_id="call"),
            Image(id="image", path=str(image_path), description="generated"),
            Message(id="assistant", role="assistant", content=[]),
        ])
        worker = Worker(
            agent,
            incoming.get,
            send,
            projections=SessionProjections({
                "display": Worker.conversation_projection,
                "remote": Worker.remote_projection,
            }),
        )
        await incoming.put(SessionPageRequest(
            id="page",
            limit=25,
            projection="display",
        ))
        await incoming.put(SessionPageRequest(
            id="remote-page",
            limit=25,
            projection="remote",
        ))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        page = next(output for output in outgoing if output.type == "session_page")
        assert [item.id for item in page.turns[0]["items"]] == [
            "user",
            "image",
            "assistant",
        ]
        remote_page = next(
            output for output in outgoing
            if output.type == "session_page" and output.request_id == "remote-page"
        )
        assert [item.id for item in remote_page.turns[0]["items"]] == [
            "user",
            "call",
            "image",
            "assistant",
        ]

    asyncio.run(test())


def test_config_snapshot_describes_loaded_plugins_even_when_inactive():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = Agent()
        agent.configs = {"model": "gpt-test", "memory": {"limit": 10}}
        agent.plugins = [SimpleNamespace(
            title="memory",
            description="Relational memory.",
            activated=False,
        )]
        worker = Worker(agent, incoming.get, send)
        await incoming.put(ConfigSnapshotRequest(id="config"))
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        snapshot = next(output for output in outgoing if output.type == "config_snapshot")
        assert snapshot["config"]["memory"] == {"limit": 10}
        assert snapshot.plugins == [{
            "name": "memory",
            "description": "Relational memory.",
            "activated": False,
        }]

    asyncio.run(test())


def test_duplicate_completed_prompt_is_not_executed_twice():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = Agent()
        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        await incoming.put(PromptRequest(id="same", prompt="hello"))
        while not any(output.type == "command_completed" for output in outgoing):
            await asyncio.sleep(0)
        await incoming.put(PromptRequest(id="same", prompt="hello"))
        await incoming.put(ShutdownRequest(id="stop"))
        await running

        assert agent.prompts == ["hello"]
        assert [output.type for output in outgoing].count("command_completed") == 2

    asyncio.run(test())


def test_developer_prompt_is_persisted_with_its_local_kind():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()

        async def send(message):
            outgoing.append(message)

        async def astream(prompt, **options):
            agent.session.append(Message(
                role=options["prompt_role"],
                kind=options["prompt_kind"],
                content=[],
            ))
            if False:
                yield None

        agent.astream = astream
        await incoming.put(PromptRequest(
            id="wake",
            prompt="<scheduled_wake />",
            prompt_role="developer",
            prompt_kind="scheduled_wake",
        ))
        await incoming.put(ShutdownRequest(id="stop"))

        await Worker(agent, incoming.get, send).run()

        assert agent.session.history[-1].role == "developer"
        assert agent.session.history[-1].kind == "scheduled_wake"

    asyncio.run(test())


def test_worker_sanitizes_interrupted_tool_calls_before_announcing_readiness():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        agent = Agent()
        agent.session.append(FunctionCall(
            name="command",
            arguments="{}",
            call_id="interrupted-call",
        ))

        async def send(message):
            outgoing.append(message)

        await incoming.put(ShutdownRequest(id="stop"))
        await Worker(agent, incoming.get, send).run()

        assert outgoing[0].type == "worker_ready"
        assert isinstance(agent.session.history[-1], FunctionCallOutput)
        assert agent.session.history[-1].call_id == "interrupted-call"

    asyncio.run(test())


def test_external_events_are_serialized_and_idempotent():
    class ExternalAgent(Agent):
        def __init__(self):
            super().__init__()
            self.external = []
            self.hooks = Hooks()
            self.hooks.add(self.on_external_realtime_turn)

        def registry(self, name):
            if name == "hooks":
                return self.hooks
            assert name == "agents"
            return self.agents

        def on_external_realtime_turn(self, payload):
            self.external.append(payload)

    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = ExternalAgent()
        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        event = ExternalEventRequest(
            id="provider-event",
            name="realtime_turn",
            payload={"transcript": "hello"},
        )
        await incoming.put(event)
        while not any(output.type == "command_completed" for output in outgoing):
            await asyncio.sleep(0)
        await incoming.put(event)
        await incoming.put(ShutdownRequest(id="stop"))
        await running

        assert agent.external == [{"transcript": "hello"}]
        assert [output.type for output in outgoing].count("command_completed") == 2

    asyncio.run(test())


def test_worker_reports_bad_requests_and_keeps_running():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        worker = Worker(Agent(), incoming.get, send)
        await incoming.put({"type": "unknown_request", "id": "bad"})
        await incoming.put(ShutdownRequest(id="stop"))

        await worker.run()

        assert [message.type for message in outgoing] == [
            "worker_ready",
            "worker_protocol_error",
            "worker_stopped",
        ]

    asyncio.run(test())


def test_transient_event_bypasses_command_queue_and_durable_receipts():
    async def test():
        outgoing = []
        observed = []
        agent = Agent()
        agent.hooks = Hooks()

        def on_external_haptic_trace(payload):
            observed.append(payload)

        agent.hooks.add(on_external_haptic_trace)

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, asyncio.Queue().get, send)
        await worker.handle(TransientEventRequest(
            id="trace-1",
            name="haptic_trace",
            payload={"runtimeId": "runtime", "samples": []},
        ))

        assert observed == [{"runtimeId": "runtime", "samples": []}]
        assert agent.session.commands == {}
        assert worker.commands.queue.empty()
        assert outgoing[-1].type == "transient_event_completed"
        assert outgoing[-1].request_id == "trace-1"
        assert outgoing[-1].result is None

    asyncio.run(test())


def test_transient_event_is_integrated_while_a_prompt_is_active():
    async def test():
        incoming = asyncio.Queue()
        outgoing = []
        prompt_started = asyncio.Event()
        release_prompt = asyncio.Event()
        trace_seen = asyncio.Event()
        agent = Agent()
        agent.hooks = Hooks()

        async def astream(prompt, **_options):
            prompt_started.set()
            await release_prompt.wait()
            yield Event.from_dict({"type": "unknown"})

        def on_external_haptic_trace(_payload):
            trace_seen.set()

        agent.astream = astream
        agent.hooks.add(on_external_haptic_trace)

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        await incoming.put(PromptRequest(id="turn", prompt="play"))
        await prompt_started.wait()
        await incoming.put(TransientEventRequest(
            id="trace-live", name="haptic_trace", payload={"samples": []},
        ))
        await asyncio.wait_for(trace_seen.wait(), timeout=1)

        assert worker.commands.active.id == "turn"
        assert agent.session.command_status("trace-live") is None
        assert any(
            output.type == "transient_event_completed"
            and output.request_id == "trace-live"
            for output in outgoing
        )

        release_prompt.set()
        while not any(
            output.type == "command_completed" and output.request_id == "turn"
            for output in outgoing
        ):
            await asyncio.sleep(0)
        await incoming.put(ShutdownRequest(id="stop"))
        await running

    asyncio.run(test())


def test_active_tool_can_await_an_application_result_while_worker_receives():
    class ApplicationAgent(Agent):
        def __init__(self):
            super().__init__()
            self.result = None

        async def astream(self, prompt):
            self.result = await self.application.call(
                "example",
                "echo",
                {"value": prompt},
            )
            yield Event.from_dict({"type": "unknown"})

    async def test():
        incoming = asyncio.Queue()
        outgoing = []

        async def send(message):
            outgoing.append(message)

        agent = ApplicationAgent()
        worker = Worker(agent, incoming.get, send)
        running = asyncio.create_task(worker.run())
        await incoming.put(PromptRequest(id="turn", prompt="hello"))

        while not any(isinstance(output, ApplicationCall) for output in outgoing):
            await asyncio.sleep(0)
        call = next(output for output in outgoing if isinstance(output, ApplicationCall))
        assert call.command_id == "turn"
        assert call.request.capability == "example"
        assert call.request.method == "echo"
        assert call.request.payload == {"value": "hello"}

        await incoming.put(ApplicationResult(
            id=call.request.id,
            status="success",
            result={"echo": "hello"},
        ))
        while not any(output.type == "command_completed" for output in outgoing):
            await asyncio.sleep(0)
        await incoming.put(ShutdownRequest(id="stop"))
        await running

        assert agent.result == {"echo": "hello"}

    asyncio.run(test())
