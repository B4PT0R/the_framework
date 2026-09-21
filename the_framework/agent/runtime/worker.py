import asyncio
import inspect
import time

from .application import ApplicationError
from .command_loop import CommandLoop
from ..context.projections import SessionProjections
from .protocol import (
    AgentConfigUpdated,
    ApplicationCall,
    ApplicationCancel,
    ApplicationResult,
    CommandAccepted,
    CommandCompleted,
    CommandEvent,
    CommandFailed,
    CompactRequest,
    ConfigSnapshot,
    ConfigSnapshotRequest,
    ConfigUpdateRequest,
    PluginBindingRequest,
    PluginBindingUpdated,
    ExternalEventRequest,
    InterruptRequest,
    PromptRequest,
    QueuedRequest,
    SessionPage,
    SessionPageRequest,
    SessionSnapshot,
    SessionSnapshotRequest,
    SessionTurn,
    ShutdownRequest,
    StateSnapshot,
    StateSnapshotRequest,
    StatusRequest,
    TransientEventCompleted,
    TransientEventRequest,
    WorkerProtocolError,
    WorkerReady,
    WorkerRequest,
    WorkerStatus,
    WorkerStopped,
)
from ..models.responses import FunctionCall, Image, Message
from ..models.usage import usage_window


class Worker:
    def __init__(
        self,
        agent,
        receive,
        send,
        *,
        command_middleware=(),
        projections=None,
        specialists=(),
    ):
        self.agent = agent
        self.receive = receive
        self.send = send
        self.command_middleware = tuple(command_middleware)
        self.projections = projections or SessionProjections()
        self.specialists = tuple(specialists)
        self.commands = CommandLoop(
            self.dispatch,
            interrupt=agent.interrupt,
            on_event=self.on_event,
            on_status=self.on_status,
        )
        self.tasks = set()
        self.live = {}
        self.application_calls = {}
        self.quota = {"quota_5h": None, "quota_7d": None, "quota_updated_at": None}
        self.quota_refresh_task = None
        if hasattr(agent, "application"):
            agent.application.bind(self.request_application)

    @staticmethod
    def conversation_projection(items):
        return [
            item for item in items
            if (
                isinstance(item, Message)
                and item.role in {"user", "assistant"}
            ) or isinstance(item, Image)
        ]

    @classmethod
    def remote_projection(cls, items):
        visible = cls.conversation_projection(items)
        return [
            item for item in items
            if item in visible or isinstance(item, FunctionCall)
        ]

    async def dispatch(self, command):
        """Run one queued command through the configured middleware chain."""
        execute = self.execute
        for middleware in reversed(self.command_middleware):
            next_execute = execute

            async def wrapped(value, current=middleware, call_next=next_execute):
                async for event in current(self.agent, value, call_next):
                    yield event

            execute = wrapped
        async for event in execute(command):
            yield event

    async def request_application(self, request):
        future = asyncio.get_running_loop().create_future()
        self.application_calls[request.id] = future
        active = self.commands.active
        command_id = active.id if active is not None else None
        await self.send(ApplicationCall(
            request_id=request.id,
            command_id=command_id,
            request=request,
        ))
        try:
            return await asyncio.wait_for(future, request.timeout_ms / 1000)
        except TimeoutError as error:
            await self.send(ApplicationCancel(
                request_id=request.id,
                command_id=command_id,
                call_id=request.id,
                reason="timeout",
            ))
            raise ApplicationError(
                f"application call timed out: {request.capability}.{request.method}"
            ) from error
        finally:
            self.application_calls.pop(request.id, None)

    async def resolve_application(self, result):
        future = self.application_calls.get(result.id)
        if future is None or future.done():
            raise ValueError(f"unknown or completed application call: {result.id}")
        if result.status == "success":
            future.set_result(result.result)
            return
        if result.status == "failure":
            future.set_exception(ApplicationError(result.error or "application call failed"))
            return
        raise ValueError(f"invalid application result status: {result.status}")

    async def on_status(self, command, status, error=None):
        await asyncio.to_thread(
            self.agent.session.set_command,
            command.id,
            status,
            error,
        )

    async def execute(self, command):
        if isinstance(command, PromptRequest):
            try:
                for item in command.input_items:
                    self.agent.add_response_item(item)
                turn_options = {}
                if not command.append_prompt:
                    turn_options["append_prompt"] = False
                if command.prompt_role != "user":
                    turn_options["prompt_role"] = command.prompt_role
                if command.prompt_kind != "message":
                    turn_options["prompt_kind"] = command.prompt_kind
                if command.reasoning_effort is not None:
                    turn_options["reasoning_effort"] = command.reasoning_effort
                stream = self.agent.astream(command.prompt, **turn_options)
                async for event in stream:
                    yield event
            finally:
                if not getattr(self.agent, "retain_history", True):
                    await asyncio.to_thread(self.agent.session.clear_context)
            return
        if isinstance(command, ExternalEventRequest):
            if command.name == "agent_result":
                agents = self.agent.registry("agents")
                profile = command.payload.get("profile")
                if profile in agents:
                    await asyncio.to_thread(agents.dispatch, command.payload)
                else:
                    async for event in self.agent.events.astream(
                        self.agent.registry("hooks").run,
                        "on_agent_result",
                        command.payload,
                    ):
                        yield event
                return
            async for event in self.agent.events.astream(
                self.agent.registry("hooks").run,
                f"on_external_{command.name}",
                command.payload,
            ):
                yield event
            return
        if isinstance(command, ConfigUpdateRequest):
            restart_required = self.agent.config_update_requires_restart(
                command.updates
            )
            config = await asyncio.to_thread(self.agent.update_config, command.updates)
            yield AgentConfigUpdated(
                config=dict(config),
                restart_required=restart_required,
            )
            return
        if isinstance(command, PluginBindingRequest):
            operation = (
                self.agent.activate_plugin
                if command.enabled
                else self.agent.deactivate_plugin
            )
            await asyncio.to_thread(operation, command.plugin)
            yield PluginBindingUpdated(
                plugin=command.plugin,
                enabled=command.enabled,
            )
            return
        if isinstance(command, CompactRequest):
            async for event in self.agent.events.astream(self.agent.compact):
                yield event
            payload = await self.agent.agentic_loop.ageneration_payload()
            self.agent.context.local_input_tokens(payload)
            return
        raise ValueError(f"unsupported queued request: {command.type}")

    async def refresh_quota(self):
        try:
            usage = await asyncio.to_thread(lambda: self.agent.client.codex.usage())
        except Exception:
            return
        self.quota = {
            "quota_5h": usage_window(
                usage,
                duration_seconds=5 * 60 * 60,
                fallback="primary_window",
            ),
            "quota_7d": usage_window(
                usage,
                duration_seconds=7 * 24 * 60 * 60,
                fallback="secondary_window",
            ),
            "quota_updated_at": int(time.time()),
        }

    def schedule_quota_refresh(self):
        if self.quota_refresh_task is not None and not self.quota_refresh_task.done():
            return
        updated_at = self.quota["quota_updated_at"] or 0
        if time.time() - updated_at < 60:
            return
        self.quota_refresh_task = asyncio.create_task(self.refresh_quota())
        self.tasks.add(self.quota_refresh_task)
        self.quota_refresh_task.add_done_callback(self.tasks.discard)

    async def on_event(self, command, event):
        await self.send(CommandEvent(
            request_id=command.id,
            command_id=command.id,
            event=event,
            watermark=self.agent.session.watermark(),
        ))

    async def completed(self, request, command):
        try:
            await command.future
        except Exception as error:
            await self.send(CommandFailed(
                request_id=request.id,
                command=command,
                error=str(error),
            ))
        else:
            await self.send(CommandCompleted(
                request_id=request.id,
                command=command,
            ))
        finally:
            self.live.pop(command.id, None)

    async def complete_steering(self, request, command):
        try:
            await command.future
        except Exception as error:
            await self.commands.set_status(command, "failed", str(error))
            await self.send(CommandFailed(
                request_id=request.id,
                command=command,
                error=str(error),
            ))
        else:
            await self.commands.set_status(command, "completed")
            await self.send(CommandCompleted(
                request_id=request.id,
                command=command,
            ))
        finally:
            self.live.pop(command.id, None)

    def can_steer(self, request):
        active = self.commands.active
        agentic_loop = getattr(self.agent, "agentic_loop", None)
        return (
            isinstance(request, PromptRequest)
            and isinstance(active, PromptRequest)
            and active.status == "active"
            and agentic_loop is not None
            and agentic_loop.can_steer()
        )

    def pending_count(self):
        agentic_loop = getattr(self.agent, "agentic_loop", None)
        return self.commands.queue.qsize() + (
            agentic_loop.pending_count if agentic_loop is not None else 0
        )

    @staticmethod
    def is_foreground(command):
        return isinstance(command, PromptRequest)

    def foreground_pending_count(self):
        return sum(
            command.status == "queued" and self.is_foreground(command)
            for command in self.live.values()
        )

    async def handle(self, request):
        request = WorkerRequest.from_dict(request)
        if isinstance(request, ApplicationResult):
            await self.resolve_application(request)
            return True
        if isinstance(request, TransientEventRequest):
            # Runtime telemetry must remain responsive while a model turn is
            # active and must never grow the durable command receipt table.
            # Synchronous hooks execute on this event loop, serially with
            # provider projection; plugin-owned locks cover tool threads.
            result = self.agent.registry("hooks").run(
                f"on_external_{request.name}", request.payload,
            )
            if inspect.isawaitable(result):
                await result
            await self.send(TransientEventCompleted(
                request_id=request.id,
            ))
            return True
        if isinstance(request, QueuedRequest):
            if request.id in self.live:
                await self.send(CommandAccepted(
                    request_id=request.id,
                    command=self.live[request.id],
                ))
                return True
            status = self.agent.session.command_status(request.id)
            if status == "completed":
                request.status = status
                await self.send(CommandCompleted(
                    request_id=request.id,
                    command=request,
                ))
                return True
            if status in {"failed", "interrupted"}:
                request.status = status
                receipt = self.agent.session.commands[request.id]
                await self.send(CommandFailed(
                    request_id=request.id,
                    command=request,
                    error=receipt.error or status,
                ))
                return True
            await asyncio.to_thread(
                self.agent.session.set_command,
                request.id,
                "queued",
            )
            if self.can_steer(request):
                command = self.commands.prepare_pending(request)
                self.live[command.id] = command
                self.agent.agentic_loop.queue_steering(
                    command.prompt,
                    append_prompt=command.append_prompt,
                    prompt_role=command.prompt_role,
                    prompt_kind=command.prompt_kind,
                    reasoning_effort=command.reasoning_effort,
                    input_items=command.input_items,
                    future=command.future,
                )
                await self.send(CommandAccepted(
                    request_id=request.id,
                    command=command,
                ))
                task = asyncio.create_task(self.complete_steering(request, command))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
                return True
            command = await self.commands.enqueue(request)
            self.live[command.id] = command
            await self.send(CommandAccepted(
                request_id=request.id,
                command=command,
            ))
            task = asyncio.create_task(self.completed(request, command))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return True
        if isinstance(request, InterruptRequest):
            await self.commands.interrupt(request.reason)
        if isinstance(request, StatusRequest) or isinstance(request, InterruptRequest):
            self.schedule_quota_refresh()
            context_limit = self.agent.configs.compaction.context_token_limit
            foreground_active = (
                self.commands.active
                if self.is_foreground(self.commands.active)
                else None
            )
            await self.send(WorkerStatus(
                request_id=request.id,
                active=self.commands.active,
                pending=self.pending_count(),
                foreground_active=foreground_active,
                foreground_pending=self.foreground_pending_count(),
                local_input_tokens=self.agent.context.last_local_input_tokens,
                context_token_limit=context_limit,
                context_saturation=(
                    self.agent.context.last_local_input_tokens / context_limit
                    if context_limit > 0
                    else 0.0
                ),
                **self.quota,
            ))
            return True
        if isinstance(request, SessionSnapshotRequest):
            snapshot = self.agent.registry("hooks").run("session_snapshot", {
                "request_id": request.id,
                "watermark": self.agent.session.watermark(),
                "items": list(self.agent.session.history),
                "archive": list(self.agent.session.archive),
                "instructions": self.agent.context.instructions(),
                "extensions": {},
            })
            await self.send(SessionSnapshot(**snapshot))
            return True
        if isinstance(request, SessionPageRequest):
            page = self.agent.session.page_archive_turns(
                before=request.before,
                limit=request.limit,
            )
            turns = [
                (turn[0].id, turn)
                for turn in page["turns"]
            ]
            turns = [
                (turn_id, self.projections.project(request.projection, turn))
                for turn_id, turn in turns
            ]
            await self.send(SessionPage(
                request_id=request.id,
                watermark=self.agent.session.watermark(),
                turns=[
                    SessionTurn(id=turn_id, items=items)
                    for turn_id, items in turns
                ],
                next_before=page["next_before"],
                has_more=page["has_more"],
            ))
            return True
        if isinstance(request, ConfigSnapshotRequest):
            await self.send(ConfigSnapshot(
                request_id=request.id,
                config=dict(self.agent.configs),
                plugins=[
                    {
                        "name": plugin.title,
                        "description": plugin.description,
                        "activated": plugin.activated,
                    }
                    for plugin in self.agent.plugins
                ],
            ))
            return True
        if isinstance(request, StateSnapshotRequest):
            await self.send(StateSnapshot(
                request_id=request.id,
                state=self.agent.state_snapshot(),
            ))
            return True
        if isinstance(request, ShutdownRequest):
            for future in self.application_calls.values():
                if not future.done():
                    future.set_exception(ApplicationError("worker is shutting down"))
            try:
                hooks = self.agent.registry("hooks")
            except AttributeError:
                hooks = None
            if hooks is not None:
                await asyncio.to_thread(
                    hooks.run,
                    "on_shutdown",
                    {"reason": "worker_shutdown"},
                )
            for plugin in reversed(getattr(self.agent, "plugins", [])):
                shutdown = getattr(plugin, "shutdown", None)
                if shutdown is None:
                    continue
                if inspect.iscoroutinefunction(shutdown):
                    await shutdown()
                else:
                    result = await asyncio.to_thread(shutdown)
                    if inspect.isawaitable(result):
                        await result
            await self.commands.close()
            return False
        raise ValueError(f"unsupported worker request: {request.type}")

    async def run(self):
        await asyncio.to_thread(self.agent.session.sanitize)
        await asyncio.to_thread(self.agent.session.recover_commands)
        plugin_specialists = (
            self.agent.registry("agents").profiles()
            if hasattr(self.agent, "agents")
            else []
        )
        specialists = {
            profile.name: profile
            for profile in (*self.specialists, *plugin_specialists)
        }
        await self.send(WorkerReady(
            session_id=self.agent.id,
            name=self.agent.name,
            description=self.agent.description,
            specialists=list(specialists.values()),
            plugins=[
                {"name": plugin.title, "activated": plugin.activated}
                for plugin in getattr(self.agent, "plugins", [])
            ],
        ))
        self.schedule_quota_refresh()
        command_task = asyncio.create_task(self.commands.run())
        while True:
            try:
                request = await self.receive()
                if not await self.handle(request):
                    break
            except Exception as error:
                await self.send(WorkerProtocolError(error=str(error)))
        await command_task
        if self.tasks:
            await asyncio.gather(*self.tasks)
        await self.send(WorkerStopped())
