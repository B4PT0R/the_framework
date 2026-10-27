"""Server-side lifecycle, bridge and event stream for one agent session."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from ...agent.context.session import Session
from ...agent.models.events import Event
from ...agent.runtime.protocol import (
    ApplicationCall,
    ApplicationCancel,
    AgentConfigUpdated,
    ConfigUpdateRequest,
    CommandAccepted,
    CommandCompleted,
    CommandEvent,
    CommandFailed,
    ExternalEventRequest,
    PromptRequest,
    PluginBindingRequest,
)
from the_framework.utils.ids import timestamp_id

from ..composition.services import ServiceContext
from .bridge import ApplicationBridge
from .fleet import FleetSupervisor
from .supervisor import WorkerExited, WorkerTransportError

logger = logging.getLogger(__name__)
_STREAM_CLOSED = object()
_SERVER_INSTANCE_ID = uuid4().hex


DEFAULT_ACTIVITY_LABELS = {
    "idle": "Available",
    "accepted": "Command accepted",
    "agent.turn.start": "Preparing turn",
    "agent.step.start": "Updating context",
    "agent.compaction.start": "Compacting context",
    "agent.generation.start": "Generating response",
    "agent.tool_calls.start": "Preparing tools",
    "agent.step.end": "Finalizing step",
    "agent.turn.end": "Finalizing turn",
    "tool": "Running {name}",
    "tool_result": "Tool result received",
    "response": "Generating response",
}


class InvalidWorkerResult(RuntimeError):
    """A completed worker command did not return its required result."""


class SessionRuntime:
    """Own worker/fleet lifecycle, IPC correlation, recovery, and event fan-out.

    Product services register observers, application capabilities, and specialist
    result handlers without subclassing the runtime. Product reset policy is
    supplied as a transaction factory because the generic runtime cannot know
    which durable resources belong to an application.
    """

    def __init__(
        self,
        supervisor,
        *,
        fleet_factory=None,
        application_subprotocol="agent-application",
        activity_labels=None,
        reset_transaction_factory=None,
        restart=None,
    ):
        self.supervisor = supervisor
        self.application = None
        self.context = None
        self.services = ServiceContext()
        self.extensions = {}
        self.service_graph = None
        self.plugin_host = None
        self.surface_service = None
        self.app = None
        self.fleet_factory = fleet_factory
        self.bridge = ApplicationBridge(
            supervisor,
            subprotocol=application_subprotocol,
        )
        self.activity_labels = {
            **DEFAULT_ACTIVITY_LABELS,
            **dict(activity_labels or {}),
        }
        self.reset_transaction_factory = reset_transaction_factory
        self.restart = restart
        self.subscribers = set()
        self.observers = set()
        self.fleet_result_handlers = set()
        self.fleet = None
        self.fleet_result_task = None
        self.fleet_delivery_task = None
        self.fleet_deliveries = asyncio.Queue()
        self.fleet_event_task = None
        self.ready = None
        self.responses = {}
        self.relay_task = None
        self.reset_lock = asyncio.Lock()
        self.resetting = False
        self.reset_hooks = []
        self.restart_hooks = []
        self.stopping = False
        self.server_restarting = False
        self.last_worker_output_at = None
        self.last_worker_output_type = None
        self.active_command_id = None
        self.activity_phase = "idle"
        self.activity_label = self.activity_labels["idle"]
        self.activity_started_at = None
        self.activity_updated_at = None
        self.subscriber_overflows = 0

    def service(self, name):
        """Resolve a running service in this session's scope."""
        return self.services.require(name)

    async def restart_server(self, intent):
        if self.restart is None:
            raise RuntimeError("server restart is not configured")
        await self.prepare_restart(intent)
        try:
            result = self.restart(intent)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            await self._publish_restart_state(False, error=str(error))
            raise

    def server_status(self):
        return {"instance_id": _SERVER_INSTANCE_ID,
                "phase": "restarting" if self.server_restarting else "ready"}

    def _restart_runtimes(self):
        if self.application is None:
            return [self]
        return [app.state.session for app, lifespan in
                tuple(self.application.session_apps.values()) if lifespan is not None]

    async def _publish_restart_state(self, restarting, *, error=None):
        for runtime in self._restart_runtimes():
            runtime.server_restarting = restarting
            await runtime.publish({
                "type": "server_restart_failed" if error is not None else "server_restarting",
                **runtime.server_status(),
                **({"error": error} if error is not None else {}),
            })

    async def prepare_restart(self, intent=None):
        """Quiesce live sessions while their workers and client sockets still exist."""
        intent = intent or {}
        await self._publish_restart_state(True)
        try:
            for runtime in self._restart_runtimes():
                for hook in tuple(runtime.restart_hooks):
                    result = hook(intent)
                    if inspect.isawaitable(result):
                        await result
                await runtime.apply_event("server_restart_barrier", {}, timeout=None)
        except Exception as error:
            await self._publish_restart_state(False, error=str(error))
            raise

    def register_restart_hook(self, hook):
        """Drain a session-owned service before the server closes client transports."""
        self.restart_hooks.append(hook)

    async def surface_notify(self, payload):
        await self.publish({**payload, "type": "interface_refresh_requested"})

    async def surface_progress(self, payload):
        await self.publish({**payload, "type": "surface_build_progress"})

    def register_observer(self, observer):
        self.observers.add(observer)
        return observer

    def unregister_observer(self, observer):
        self.observers.discard(observer)

    def register_fleet_result_handler(self, handler):
        self.fleet_result_handlers.add(handler)
        return handler

    def unregister_fleet_result_handler(self, handler):
        self.fleet_result_handlers.discard(handler)

    def register_reset_hooks(
        self, *, suspend: Callable[[], object] | None = None,
        resume: Callable[[], object] | None = None,
    ):
        """Close, pause or refresh plugin resources around a session reset."""
        if suspend is None and resume is None:
            raise ValueError("at least one reset hook is required")
        if any(hook is not None and not callable(hook) for hook in (suspend, resume)):
            raise TypeError("reset hooks must be callable")
        self.reset_hooks.append((suspend, resume))

    def plugin_bindings(self):
        """Agent binding states confirmed by the current worker startup."""
        if self.ready is None:
            raise RuntimeError("canonical worker is not ready")
        return {
            item["name"]: item["activated"]
            for item in self.ready.plugins
        }

    async def set_plugin_binding(self, name: str, enabled: bool, *, call: ApplicationCall | None = None) -> None:
        """Apply a binding in the worker, including from a suspended agent tool."""
        result = await self.command(PluginBindingRequest(
            id=timestamp_id(), plugin=name, enabled=enabled,
            application_call_id=call.request.id if call is not None else None,
        ))
        if result is None or result.type != "agent.plugin.binding.updated":
            raise RuntimeError("worker did not confirm the plugin binding update")

    async def start(self):
        self.stopping = False
        self.ready = await self.supervisor.start()
        try:
            self.fleet = (
                self.fleet_factory(self.supervisor.session_path, self.ready.specialists)
                if self.fleet_factory is not None else FleetSupervisor(
                    self.supervisor.session_path, self.ready.specialists,
                    application_reference=getattr(self.supervisor, "application_reference", None),
                    application_root=getattr(self.supervisor, "application_root", None),
                )
            )
            await self.fleet.start()
            self.register_observer(self.fleet.observe)
            self.fleet_delivery_task = asyncio.create_task(
                self.deliver_fleet_results(), name="agent-fleet-delivery",
            )
            self.fleet_result_task = asyncio.create_task(
                self.relay_fleet_results(), name="agent-fleet-results",
            )
            if getattr(self.fleet, "events", None) is not None:
                self.fleet_event_task = asyncio.create_task(self.relay_fleet_events(), name="subagent-events")
            self.relay_task = asyncio.create_task(self.relay(), name="agent-worker-relay")
            await self.bridge.start()
            await self.broadcast_worker_directory()
        except BaseException as startup_error:
            try:
                await self.stop()
            except BaseException as cleanup_error:  # noqa: BLE001 — preserve cancellation and cleanup failure.
                raise BaseExceptionGroup(
                    "application startup and cleanup failed", [startup_error, cleanup_error],
                ) from None
            raise
        return self.ready

    async def stop(self):
        self.stopping = True
        for response in self.responses.values():
            if not response.done():
                response.set_exception(RuntimeError("application runtime stopped"))
        self.responses.clear()
        await self.publish(WorkerExited(returncode=0, stderr=[]))
        if self.fleet is not None:
            self.unregister_observer(self.fleet.observe)
        tasks = (self.fleet_result_task, self.fleet_delivery_task, self.fleet_event_task, self.relay_task)
        for task in tasks:
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task),
            return_exceptions=True,
        )
        self.fleet_result_task = self.fleet_delivery_task = self.fleet_event_task = self.relay_task = None
        self.fleet_deliveries = asyncio.Queue()
        errors = []
        for service in (self.bridge, self.fleet, self.supervisor):
            if service is None:
                continue
            try:
                await service.stop()
            except Exception as error:  # noqa: BLE001 — finish all owners before reporting failures.
                errors.append(error)
        while not self.supervisor.outputs.empty():
            self.supervisor.outputs.get_nowait()
        if errors:
            raise ExceptionGroup("application runtime shutdown failed", errors)

    async def reset_session(self):
        """Application-owned HTTP reset policy, deliberately unavailable by default.

        Override this method to select a transaction through reset_runtime_state
        and return the application's reset result. A transaction factory alone
        does not opt an application into destructive HTTP operations.
        """
        raise NotImplementedError("Canonical session reset is not configured")

    async def reset_runtime_state(
        self, *, transaction_factory: Callable[[Path], object] | None = None,
    ):
        """Reset state with suspension and rollback.

        An optional per-call transaction selects application-owned resources
        without changing the default recipe or the lifecycle hooks.
        """
        factory = self.reset_transaction_factory if transaction_factory is None else transaction_factory
        if factory is None:
            raise RuntimeError("application runtime reset is not configured")
        async with self.reset_lock:
            self.resetting = True
            resumes = []
            result = None
            failure = None
            try:
                for suspend, resume in reversed(tuple(self.reset_hooks)):
                    if suspend is not None:
                        value = suspend()
                        if inspect.isawaitable(value):
                            await value
                    if resume is not None:
                        resumes.append(resume)
                result = await self._reset_canonical_runtime(factory)
            except BaseException as error:
                failure = error
            finally:
                resume_errors = []
                for resume in reversed(resumes):
                    try:
                        value = resume()
                        if inspect.isawaitable(value):
                            await value
                    except BaseException as error:
                        resume_errors.append(error)
                self.resetting = False
            if failure is not None and resume_errors:
                raise BaseExceptionGroup(
                    "application reset and service recovery failed",
                    [failure, *resume_errors],
                )
            if failure is not None:
                raise failure
            if resume_errors:
                raise BaseExceptionGroup(
                    "application services failed to resume after reset",
                    resume_errors,
                )
            return result

    async def _reset_canonical_runtime(self, transaction_factory):
        await self.stop()
        path = Path(self.supervisor.session_path)
        try:
            previous = Session.open(path)
        except ValueError:
            logger.warning("Resetting a session with unreadable settings")
            previous = Session()
        try:
            staged = transaction_factory(
                self.supervisor.session_path,
            ).stage()
        except Exception as stage_error:
            try:
                await self.start()
            except Exception as recovery_error:
                raise RuntimeError(
                    "application reset staging failed and the previous runtime "
                    f"could not restart: {recovery_error}"
                ) from stage_error
            raise
        try:
            if not path.exists():
                replacement = Session(
                    **({"id": self.supervisor.session_id} if getattr(self.supervisor, "session_id", None) else {}),
                    plugins=dict(previous.plugins), configuration=dict(previous.configuration),
                    state=dict(previous.state),
                )
                replacement.set_attr("path", path)
                replacement.save()
            await self.start()
        except Exception as reset_error:
            try:
                await self.stop()
            except Exception:
                pass
            staged.restore(remove_new=True)
            try:
                await self.start()
            except Exception as recovery_error:
                raise RuntimeError(
                    "application reset failed and the previous runtime "
                    f"could not restart: {recovery_error}"
                ) from reset_error
            raise RuntimeError(
                "application reset failed; the previous state was restored"
            ) from reset_error
        staged.commit()
        return self.ready

    async def relay_fleet_events(self):
        while True:
            event = await self.fleet.events.get()
            try:
                await self.publish(event)
            finally:
                self.fleet.events.task_done()

    async def relay_fleet_results(self):
        while True:
            result = await self.fleet.results.get()
            try:
                consumed = False
                for handler in tuple(self.fleet_result_handlers):
                    try:
                        handled = handler(result)
                        if inspect.isawaitable(handled):
                            handled = await handled
                        consumed = handled is True or consumed
                    except Exception:
                        logger.exception("specialist result handler failed")
                if consumed:
                    await self.fleet.acknowledge(result)
                else:
                    # Canonical delivery may wait for the calling turn. Keep routing
                    # inline replies while that separate, ordered consumer waits.
                    self.fleet_deliveries.put_nowait(result)
            finally:
                self.fleet.results.task_done()

    async def deliver_fleet_results(self):
        while True:
            result = await self.fleet_deliveries.get()
            try:
                while True:
                    try:
                        await self.command(ExternalEventRequest(
                            id=f"agent-result-{result.id}",
                            name="agent_result", payload=result,
                        ))
                        await self.fleet.acknowledge(result)
                        break
                    except Exception:
                        # The durable outbox remains unacknowledged until delivery.
                        logger.exception("specialist result delivery failed; retrying")
                        await asyncio.sleep(1)
            finally:
                self.fleet_deliveries.task_done()

    def worker_directory(self):
        return [
            {
                "id": self.ready.session_id,
                "name": self.ready.name,
                "description": self.ready.description,
            },
            *self.fleet.directory(),
        ]

    async def broadcast_worker_directory(self):
        if self.context is not None and self.context.get("validation", False):
            return
        payload = {"workers": self.worker_directory()}
        for worker in payload["workers"]:
            request = ExternalEventRequest(
                id=f"worker-directory-{worker['id']}-{timestamp_id()}",
                name="worker_directory",
                payload=payload,
            )
            if worker["id"] == self.ready.session_id:
                await self.supervisor.send(request)
            else:
                await self.fleet.send_to(worker["id"], request)

    async def relay(self):
        while True:
            output = await self.supervisor.outputs.get()
            self.observe_activity(output)
            if isinstance(output, WorkerTransportError):
                logger.error("canonical worker transport failed: %s", output.error)
                await self.publish(output)
                continue
            if isinstance(output, WorkerExited):
                logger.error(
                    "canonical worker exited with code %s; stderr tail: %s",
                    output.returncode,
                    " | ".join(output.stderr[-20:]) or "<empty>",
                )
                await self.publish(output)
                for response in self.responses.values():
                    if not response.done():
                        response.set_exception(RuntimeError("worker exited"))
                self.responses.clear()
                if not self.stopping and not self.resetting:
                    await self.recover_worker()
                continue
            for observer in tuple(self.observers):
                try:
                    observed = observer(output)
                    if inspect.isawaitable(observed):
                        await observed
                except Exception:
                    logger.exception("worker output observer failed: %r", observer)
            response = self.responses.pop(output.request_id, None)
            if response is not None and not response.done():
                response.set_result(output)
                continue
            if isinstance(output, (ApplicationCall, ApplicationCancel)):
                await self.bridge.dispatch(output)
                continue
            await self.publish(output)

    async def recover_worker(self):
        await self.bridge.stop()
        delay = 0.25
        while not self.stopping and not self.resetting:
            try:
                self.ready = await self.supervisor.restart()
            except Exception as error:
                logger.exception("canonical worker restart failed")
                await self.publish(WorkerTransportError(
                    error=f"worker restart failed: {error}",
                ))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
                continue
            self.active_command_id = None
            self.activity_phase = "idle"
            self.activity_label = self.activity_labels["idle"]
            self.activity_started_at = None
            self.activity_updated_at = time.time()
            await self.bridge.start()
            await self.broadcast_worker_directory()
            logger.info("canonical worker restarted after unexpected exit")
            return

    def observe_activity(self, output):
        now = time.time()
        self.last_worker_output_at = now
        self.last_worker_output_type = getattr(
            output, "type", type(output).__name__,
        )
        if isinstance(output, CommandAccepted):
            if isinstance(output.command, PromptRequest) and self.active_command_id is None:
                self.active_command_id = output.command.id
                self.activity_phase = "queued"
                self.activity_label = self.activity_labels["accepted"]
                self.activity_started_at = now
                self.activity_updated_at = now
            return
        if isinstance(output, CommandEvent):
            if output.command_id != self.active_command_id:
                return
            event_type = output.event.get("type", "")
            phases = {
                "agent.turn.start": "preparing",
                "agent.step.start": "providers",
                "agent.compaction.start": "compaction",
                "agent.generation.start": "model",
                "agent.tool_calls.start": "tools",
                "agent.step.end": "finalizing",
                "agent.turn.end": "finalizing",
            }
            phase = phases.get(event_type, self.activity_phase)
            label = self.activity_labels.get(event_type, self.activity_label)
            if event_type == "agent.tool_call.start":
                tool_call = output.event.get("tool_call") or {}
                feedback = str(output.event.get("user_feedback") or "").strip()
                name = str(tool_call.get("name") or "tool")
                phase = "tool"
                label = feedback or self.activity_labels["tool"].format(name=name)
            elif event_type == "agent.tool_call.end":
                phase, label = "tools", self.activity_labels["tool_result"]
            elif event_type in {"agent.text.delta", "agent.text.done", "agent.output_item.started",
                                "agent.tool_input.delta", "agent.tool_input.done", "agent.remote_tool.status"}:
                phase, label = "model", self.activity_labels["response"]
            self.active_command_id = output.command_id
            self.activity_phase = phase
            self.activity_label = label
            self.activity_started_at = self.activity_started_at or now
            self.activity_updated_at = now
            return
        if isinstance(output, (CommandCompleted, CommandFailed)):
            if self.active_command_id in {None, output.command.id}:
                self.active_command_id = None
                self.activity_phase = "idle"
                self.activity_label = self.activity_labels["idle"]
                self.activity_started_at = None
                self.activity_updated_at = now

    def activity_status(self):
        now = time.time()
        return {
            "command_id": self.active_command_id,
            "phase": self.activity_phase,
            "label": self.activity_label,
            "started_at": self.activity_started_at,
            "updated_at": self.activity_updated_at,
            "silent_for_seconds": (
                max(0.0, now - self.activity_updated_at)
                if self.active_command_id is not None
                and self.activity_updated_at is not None
                else 0.0
            ),
            "last_worker_output_at": self.last_worker_output_at,
            "last_worker_output_type": self.last_worker_output_type,
            "subscriber_overflows": self.subscriber_overflows,
        }

    async def publish(self, output):
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(output)
            except asyncio.QueueFull:
                self.subscriber_overflows += 1
                self.subscribers.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(_STREAM_CLOSED)

    def health(self) -> dict:
        """Report worker readiness and live application diagnostics."""
        return {
            "status": "ok" if self.supervisor.running and self.ready is not None else "degraded",
            "worker": {
                "running": self.supervisor.running,
                "session_id": self.ready.session_id if self.ready else None,
                "protocol": self.ready.protocol if self.ready else None,
            },
            "activity": self.activity_status(),
            "application": {"connected": self.bridge.connected},
            "auxiliaries": self.fleet.status() if self.fleet else [],
        }

    async def update_config(self, updates: dict, *, id: str | None = None,
                            timeout: float = 10) -> AgentConfigUpdated:
        """Validate and persist configuration through the sole history writer."""
        result = await self.command(
            ConfigUpdateRequest(id=id or timestamp_id(), updates=updates), timeout=timeout,
        )
        if not isinstance(result, AgentConfigUpdated):
            raise InvalidWorkerResult("Agent completed the config update without a result")
        return result

    async def apply_event(self, name: str, payload: object, *, id: str | None = None,
                          timeout: float = 10) -> Event | None:
        """Wait for a durable, ordered event; reuse its id when retrying."""
        return await self.command(
            ExternalEventRequest(id=id or timestamp_id(), name=name, payload=payload),
            timeout=timeout,
        )

    async def request(self, payload, timeout=5):
        if self.stopping:
            raise RuntimeError("application runtime stopped")
        if self.resetting:
            raise RuntimeError("application reset is in progress")
        if payload.id in self.responses:
            raise ValueError(f"duplicate worker request: {payload.id}")
        response = asyncio.get_running_loop().create_future()
        self.responses[payload.id] = response
        try:
            await self.supervisor.send(payload)
            return await asyncio.wait_for(response, timeout)
        finally:
            self.responses.pop(payload.id, None)

    async def submit(self, payload):
        """Submit a command without waiting for its eventual completion."""
        if self.stopping:
            raise RuntimeError("application runtime stopped")
        if self.resetting:
            raise RuntimeError("application reset is in progress")
        await self.supervisor.send(payload)
        return payload

    async def command(self, payload, timeout=10):
        if self.stopping:
            raise RuntimeError("application runtime stopped")
        if self.resetting:
            raise RuntimeError("application reset is in progress")
        queue = asyncio.Queue()
        self.subscribers.add(queue)
        result = None
        try:
            await self.supervisor.send(payload)
            async with asyncio.timeout(timeout):
                while True:
                    output = await queue.get()
                    if output is _STREAM_CLOSED or isinstance(output, WorkerExited):
                        raise RuntimeError("application runtime stopped")
                    if isinstance(output, WorkerTransportError):
                        raise RuntimeError(f"worker transport failed: {output.error}")
                    if not isinstance(output, (CommandEvent, CommandCompleted, CommandFailed)):
                        continue
                    if output.request_id != payload.id:
                        continue
                    if isinstance(output, CommandEvent):
                        result = output.event
                    elif isinstance(output, CommandCompleted):
                        return result
                    elif isinstance(output, CommandFailed):
                        raise ValueError(output.error)
        finally:
            self.subscribers.discard(queue)

    async def stream(self):
        if self.stopping:
            return
        queue = asyncio.Queue(512)
        self.subscribers.add(queue)
        try:
            while True:
                output = await queue.get()
                if output is _STREAM_CLOSED:
                    return
                yield output
        finally:
            self.subscribers.discard(queue)
