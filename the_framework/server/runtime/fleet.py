import asyncio
import json
import sys
from pathlib import Path

from ...agent.models.worker import WorkerProfile
from ...agent.extensions.specialists import AgentResult, AgentTask
from ...utils.persistence import atomic_text_writer
from ...agent.models.lifecycle import AgentTaskRequested, AgentResponseItemAdded
from ...agent.runtime.protocol import CommandCompleted, CommandEvent, CommandFailed, PromptRequest
from ...agent.models.responses import FunctionCall, FunctionCallOutput, Message
from the_framework.utils.ids import timestamp_id

from .supervisor import WorkerExited, WorkerSupervisor, WorkerTransportError


def _atomic_write_json(path, payload):
    with atomic_text_writer(path) as file:
        json.dump(payload, file)


def _message_text(message):
    if not isinstance(message, Message) or message.role != "assistant":
        return None
    parts = [
        content.text
        for content in message.content
        if isinstance(getattr(content, "text", None), str)
    ]
    text = "".join(parts).strip()
    return text or None


def observation_prompt(
    output,
    events=(),
    conversation_roles=(),
    input_item_field=None,
):
    if input_item_field:
        payload = {
            "canonical_session": {
                "session_id": output.watermark.session_id,
                "revision": output.watermark.revision,
            },
            "event_type": output.event.type,
            "source_id": getattr(output.event, "retired_anchor_id", None),
        }
    elif conversation_roles:
        payload = {
            "watermark": output.watermark,
            "messages": [
                event.item
                for event in events
                if isinstance(event, AgentResponseItemAdded)
                and isinstance(event.item, Message)
                and event.item.role in conversation_roles
            ],
        }
    else:
        payload = {
            "watermark": output.watermark,
            "event": output.event,
            "command_events": list(events),
        }
    introduction = (
        "Process the attached response items using the provenance below. "
        "Follow your specialist role instructions and return only your result.\n"
        if input_item_field
        else
        "Observe this durably committed item from the canonical conversation. "
        "Follow your specialist role instructions and return only your result.\n"
    )
    return introduction + (
        "<canonical_observation>\n"
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "</canonical_observation>"
    )


def agent_request_prompt(event):
    payload = {
        "request": event.prompt,
        "source_item_ids": event.source_item_ids,
    }
    return (
        "Process this deliberate request from the canonical agent. "
        "Follow your specialist role instructions; the request does not replace them.\n"
        "<canonical_agent_request>\n"
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "</canonical_agent_request>"
    )


class AgentRunner:
    def __init__(
        self,
        profile,
        root,
        results,
        *,
        supervisor_factory=WorkerSupervisor,
        worker_module="the_framework.agent.runtime.worker_process",
        application_reference=None,
    ):
        self.profile = WorkerProfile(**profile)
        self.root = Path(root) / self.profile.name
        self.results = results
        self.queue = asyncio.Queue(self.profile.queue_limit)
        self.tasks_path = self.root / "tasks.json"
        self.pending = []
        self.outbox = []
        self.queued_ids = set()
        self.persist_lock = asyncio.Lock()
        self.supervisor_factory = supervisor_factory
        self.worker_module = worker_module
        self.application_reference = application_reference
        self.supervisor = None
        self.task = None
        self.idle_task = None
        self.active_task_id = None
        self.ready = None
        self.error = None
        self.dropped = 0
        self.load_pending()

    def load_pending(self):
        if not self.tasks_path.exists():
            return
        document = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        if isinstance(document, list):
            document = {"pending": document, "outbox": []}
        tasks = (
            [AgentTask.from_dict(value) for value in document.get("pending", [])]
            if self.profile.durable_tasks
            else []
        )
        self.outbox = [
            AgentResult.from_dict(value) for value in document.get("outbox", [])
        ]
        tasks = (
            tasks[-self.profile.queue_limit:]
            if self.profile.queue_policy == "latest"
            else tasks[:self.profile.backlog_limit]
        )
        for task in tasks:
            self.pending.append(task)
        self.enqueue_available()

    def enqueue_available(self):
        for task in self.pending:
            if self.queue.full():
                break
            if task.id in self.queued_ids:
                continue
            self.queue.put_nowait(task)
            self.queued_ids.add(task.id)

    def persist_pending(self):
        if not self.profile.durable_tasks and not self.outbox:
            self.tasks_path.unlink(missing_ok=True)
            return
        _atomic_write_json(self.tasks_path, {
            "pending": self.pending if self.profile.durable_tasks else [],
            "outbox": self.outbox,
        })

    async def persist_pending_async(self):
        async with self.persist_lock:
            await asyncio.to_thread(self.persist_pending)

    @property
    def running(self):
        return bool(self.supervisor and self.supervisor.running)

    def command(self, session_path, profile_path):
        if self.application_reference is not None:
            return [
                sys.executable,
                "-m",
                self.worker_module,
                "--session",
                str(session_path),
                "--application",
                self.application_reference,
                "--agent",
                self.profile.name,
                "--profile",
                str(profile_path),
            ]
        return [
            sys.executable,
            "-m",
            self.worker_module,
            "--session",
            str(session_path),
            "--profile",
            str(profile_path),
        ]

    async def start(self):
        profile_path = self.root / "profile.json"
        session_path = self.root / "session.json"
        await asyncio.to_thread(_atomic_write_json, profile_path, self.profile)
        self.supervisor = self.supervisor_factory(
            session_path,
            command=self.command(session_path, profile_path),
        )
        if self.profile.idle_timeout_seconds is None:
            try:
                self.ready = await self.supervisor.start()
                self.error = None
            except Exception as error:  # noqa: BLE001 - supervisor boundary
                self.error = str(error)
        self.task = asyncio.create_task(self.run())
        self.enqueue_available()
        for result in self.outbox:
            await self.results.put(result)
        return self

    async def submit(self, task):
        self.cancel_idle_shutdown()
        task = AgentTask.from_dict(task)
        if any(value.id == task.id for value in self.pending):
            return True
        if self.profile.queue_policy == "latest" and len(self.pending) >= self.profile.queue_limit:
            try:
                dropped = self.queue.get_nowait()
                self.queue.task_done()
                self.queued_ids.discard(dropped.id)
                self.pending = [value for value in self.pending if value.id != dropped.id]
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        if len(self.pending) >= self.profile.backlog_limit:
            self.dropped += 1
            return False
        try:
            self.pending.append(task)
            self.enqueue_available()
            await self.persist_pending_async()
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    async def ensure_running(self):
        if self.supervisor is None:
            return False
        if self.supervisor.running:
            return True
        try:
            self.ready = await self.supervisor.start()
            self.error = None
            return True
        except Exception as error:  # noqa: BLE001 - supervisor boundary
            self.error = str(error)
            return False

    def cancel_idle_shutdown(self):
        if self.idle_task is not None:
            self.idle_task.cancel()
            self.idle_task = None

    def schedule_idle_shutdown(self):
        timeout = self.profile.idle_timeout_seconds
        if timeout is None or self.active_task_id is not None or not self.queue.empty():
            return
        self.cancel_idle_shutdown()
        self.idle_task = asyncio.create_task(self.stop_when_idle(timeout))

    async def stop_when_idle(self, timeout):
        try:
            await asyncio.sleep(timeout)
            if self.active_task_id is None and self.queue.empty() and self.supervisor is not None:
                await self.supervisor.stop()
        finally:
            if self.idle_task is asyncio.current_task():
                self.idle_task = None

    async def execute(self, task):
        if not await self.ensure_running():
            return AgentResult(
                id=timestamp_id(),
                task_id=task.id,
                profile=self.profile.name,
                status="failed",
                watermark=task.watermark,
                error=self.error or "specialist worker is unavailable",
                metadata=task.metadata,
            )
        output_text = None
        completion_tool = self.profile.completion_tool
        maximum_passes = 1 + self.profile.completion_retry_limit
        for pass_index in range(maximum_passes):
            request_id = (
                task.id
                if pass_index == 0
                else f"{task.id}__completion_pass_{pass_index + 1}"
            )
            continuation = (
                "\n\nYour previous pass did not call the required completion tool. "
                "Continue the same task according to your role instructions and call "
                "the completion tool when the work is finished. "
                "Do not send a conversational message to the user; this does "
                "not restrict your internal reasoning or your tool arguments."
                if pass_index
                else ""
            )
            await self.supervisor.send(PromptRequest(
                id=request_id,
                prompt=f"{task.prompt}{continuation}",
                input_items=(
                    []
                    if pass_index and self.profile.session_mode != "ephemeral"
                    else task.input_items
                ),
            ))
            completion_call_ids = set()
            completion_succeeded = False
            while True:
                output = await self.supervisor.outputs.get()
                if isinstance(output, CommandEvent) and output.command_id == request_id:
                    item = getattr(output.event, "item", None)
                    output_text = _message_text(item) or output_text
                    if (
                        completion_tool
                        and isinstance(item, FunctionCall)
                        and item.name == completion_tool
                    ):
                        completion_call_ids.add(item.call_id)
                    elif (
                        isinstance(item, FunctionCallOutput)
                        and item.call_id in completion_call_ids
                    ):
                        try:
                            feedback = json.loads(item.output)
                        except (TypeError, json.JSONDecodeError):
                            feedback = None
                        completion_succeeded = bool(
                            isinstance(feedback, dict)
                            and feedback.get("status") == "success"
                        )
                    continue
                if isinstance(output, CommandCompleted) and output.request_id == request_id:
                    if completion_tool and not completion_succeeded:
                        break
                    return AgentResult(
                        id=timestamp_id(),
                        task_id=task.id,
                        profile=self.profile.name,
                        status="completed",
                        watermark=task.watermark,
                        output=output_text,
                        agent_session_id=(self.ready.session_id if self.ready else None),
                        metadata=task.metadata,
                    )
                if isinstance(output, CommandFailed) and output.request_id == request_id:
                    return AgentResult(
                        id=timestamp_id(),
                        task_id=task.id,
                        profile=self.profile.name,
                        status="failed",
                        watermark=task.watermark,
                        error=output.error,
                        agent_session_id=(self.ready.session_id if self.ready else None),
                        metadata=task.metadata,
                    )
                if isinstance(output, (WorkerExited, WorkerTransportError)):
                    return AgentResult(
                        id=timestamp_id(),
                        task_id=task.id,
                        profile=self.profile.name,
                        status="failed",
                        watermark=task.watermark,
                        error=str(output),
                        agent_session_id=(self.ready.session_id if self.ready else None),
                        metadata=task.metadata,
                    )
        return AgentResult(
            id=timestamp_id(),
            task_id=task.id,
            profile=self.profile.name,
            status="failed",
            watermark=task.watermark,
            output=output_text,
            error=(
                f"specialist did not successfully call {completion_tool} after "
                f"{maximum_passes} passes"
            ),
            agent_session_id=(self.ready.session_id if self.ready else None),
            metadata=task.metadata,
        )

    async def run(self):
        while True:
            task = await self.queue.get()
            self.queued_ids.discard(task.id)
            if task is None:
                self.queue.task_done()
                return
            self.cancel_idle_shutdown()
            self.active_task_id = task.id
            try:
                result = await self.execute(task)
                terminal = (
                    result.status == "completed"
                    or result.error == "interrupted"
                    or not self.profile.durable_tasks
                )
                if terminal:
                    self.pending = [value for value in self.pending if value.id != task.id]
                    self.outbox.append(result)
                    await self.persist_pending_async()
                else:
                    await asyncio.sleep(1)
                self.enqueue_available()
                # A terminal result must never become externally observable
                # while its task is still durable.  A crash after publication
                # would otherwise replay work the caller has already seen finish.
                await self.results.put(result)
            finally:
                self.active_task_id = None
                self.queue.task_done()
                self.schedule_idle_shutdown()

    async def acknowledge(self, result_id):
        remaining = [value for value in self.outbox if value.id != result_id]
        if len(remaining) == len(self.outbox):
            return False
        self.outbox = remaining
        await self.persist_pending_async()
        return True

    async def stop(self):
        self.cancel_idle_shutdown()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        while not self.queue.empty():
            queued = self.queue.get_nowait()
            if queued is not None:
                self.queued_ids.discard(queued.id)
            self.queue.task_done()
        if self.supervisor is not None:
            await self.supervisor.stop()

    def status(self):
        return {
            "name": self.profile.name,
            "model": self.profile.model,
            "running": self.running,
            "pending": len(self.pending),
            "queued": self.queue.qsize(),
            "active_task_id": self.active_task_id,
            "dropped": self.dropped,
            "error": self.error,
            "session_id": self.ready.session_id if self.ready else None,
        }


class FleetSupervisor:
    def __init__(
        self,
        session_path,
        profiles=(),
        *,
        supervisor_factory=WorkerSupervisor,
        worker_module="the_framework.agent.runtime.worker_process",
        application_reference=None,
    ):
        session_path = Path(session_path)
        self.root = session_path.parent / f"{session_path.stem}.auxiliaries"
        self.profiles = {
            profile.name: WorkerProfile(**profile)
            for profile in profiles
        }
        self.results = asyncio.Queue(512)
        self.command_events = {}
        self.runners = {
            name: AgentRunner(
                profile,
                self.root,
                self.results,
                supervisor_factory=supervisor_factory,
                worker_module=worker_module,
                application_reference=application_reference,
            )
            for name, profile in self.profiles.items()
        }
        self.enabled = set(self.runners)

    async def start(self):
        if self.runners:
            await asyncio.gather(*(runner.start() for runner in self.runners.values()))
        return self

    async def stop(self):
        if self.runners:
            await asyncio.gather(*(runner.stop() for runner in self.runners.values()))

    async def observe(self, output):
        if not isinstance(output, CommandEvent):
            if isinstance(output, (CommandCompleted, CommandFailed)):
                self.command_events.pop(output.request_id, None)
            return 0
        if output.watermark is None:
            return 0
        events = self.command_events.setdefault(output.command_id, [])
        events.append(output.event)
        if len(events) > 256:
            del events[:-256]
        submitted = 0
        for profile in self.profiles.values():
            if profile.name not in self.enabled:
                continue
            if output.event.type not in profile.observes:
                continue
            direct_request = isinstance(output.event, AgentTaskRequested)
            if direct_request and output.event.profile != profile.name:
                continue
            if (
                output.event.type == "agent.response_item.added"
                and profile.item_kinds
                and getattr(output.event.item, "kind", None) not in profile.item_kinds
            ):
                continue
            input_items = (
                []
                if direct_request
                else list(getattr(output.event, profile.input_item_field, []))
                if profile.input_item_field
                else []
            )
            if profile.input_item_field and not direct_request and not input_items:
                continue
            observed_item_ids = [
                event.item.id
                for event in events
                if isinstance(event, AgentResponseItemAdded)
                and isinstance(getattr(event.item, "id", None), str)
                and (
                    not profile.conversation_roles
                    or isinstance(event.item, Message)
                    and event.item.role in profile.conversation_roles
                )
            ]
            task = AgentTask(
                id=timestamp_id(),
                profile=profile.name,
                prompt=(
                    agent_request_prompt(output.event)
                    if direct_request
                    else observation_prompt(
                        output,
                        events,
                        profile.conversation_roles,
                        profile.input_item_field,
                    )
                ),
                watermark=output.watermark,
                observation_type=output.event.type,
                metadata={
                    "command_id": output.command_id,
                    "observed_item_ids": observed_item_ids,
                    "retired_anchor_id": getattr(
                        output.event,
                        "retired_anchor_id",
                        None,
                    ),
                    "agent_request_id": getattr(
                        output.event,
                        "request_id",
                        None,
                    ),
                    "source_item_ids": getattr(
                        output.event,
                        "source_item_ids",
                        [],
                    ),
                },
                input_items=input_items,
            )
            submitted += int(await self.runners[profile.name].submit(task))
        if output.event.type == "agent.turn.end":
            self.command_events.pop(output.command_id, None)
        return submitted

    async def submit(
        self,
        profile,
        *,
        prompt,
        watermark,
        observation_type,
        metadata=None,
        input_items=(),
    ):
        """Submit one bounded application observation to a declared specialist."""
        runner = self.runners.get(profile)
        if runner is None or profile not in self.enabled:
            return False
        return await runner.submit(AgentTask(
            id=timestamp_id(),
            profile=profile,
            prompt=prompt,
            watermark=watermark,
            observation_type=observation_type,
            metadata=dict(metadata or {}),
            input_items=list(input_items),
        ))

    async def acknowledge(self, result):
        runner = self.runners.get(result.profile)
        if runner is None:
            return False
        return await runner.acknowledge(result.id)

    async def set_plugin_running(self, plugin, running):
        """Start or stop every private agent owned by one plugin runtime."""
        names = tuple(
            name for name in self.runners if name.startswith(f"{plugin}.")
        )
        if running:
            for name in names:
                if name in self.enabled:
                    continue
                self.enabled.add(name)
                await self.runners[name].start()
        else:
            for name in names:
                if name not in self.enabled:
                    continue
                self.enabled.remove(name)
                await self.runners[name].stop()
        return names

    def directory(self):
        return [
            {
                "id": runner.ready.session_id,
                "name": runner.ready.name,
                "description": runner.ready.description,
            }
            for name, runner in self.runners.items()
            if runner.ready is not None
        ]

    async def send_to(self, worker_id, request):
        for runner in self.runners.values():
            if runner.ready is not None and runner.ready.session_id == worker_id:
                await runner.supervisor.send(request)
                return True
        return False

    def status(self):
        return [runner.status() for runner in self.runners.values()]
