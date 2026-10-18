"""Neutral voice surface over the framework's canonical realtime controller."""

import asyncio
import logging

from the_framework import endpoint
from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, SessionSnapshotRequest, StatusRequest
from the_framework.server.api.endpoints import HttpError
from the_framework.utils.ids import timestamp_id

from .events import (RealtimeControlFailed, RealtimeControlRequested,
                     RealtimeReconnectRequested, RealtimeRestarting)

CALL_REQUEST = {"type": "object", "properties": {
    "call_id": {"type": "string", "minLength": 1, "maxLength": 200},
}, "required": ["call_id"], "additionalProperties": False}


logger = logging.getLogger(__name__)


class RealtimeApi:
    def __init__(self, runtime, controller, transport=None):
        self.runtime = runtime
        self.transport = transport
        self.control_request = None
        self.control_task = None
        self.controller = controller
        self.lock = asyncio.Lock()
        self.call_id = None
        self._reconnect_tasks = set()
        self.restarting = False

    async def handle(self, method, payload, *, command_id=None):
        if method == "status":
            return {"active": self.controller.active, "call_id": self.call_id,
                    "pending": dict(self.control_request) if self.control_request else None}
        if method not in {"start", "stop"}:
            raise ValueError(f"unsupported realtime method: {method}")
        if command_id is None:
            raise ValueError("voice control requires an active agent command")
        if self.restarting:
            raise RuntimeError("The server is restarting")
        if self.control_request is not None:
            raise RuntimeError("A voice control request is already pending")
        if method == "start":
            if self.controller.active:
                return {"status": "already_active", "call_id": self.call_id}
            if self.transport is None or not self.transport.primary_event_connection_count:
                raise RuntimeError("The primary interface is not connected")
        elif not self.controller.active:
            return {"status": "already_stopped"}
        self.control_request = {
            "action": method, "command_id": command_id, "call_id": self.call_id,
        }
        return {"status": "voice_control_requested", **self.control_request}

    def observe(self, output):
        request = self.control_request
        if (request is None or self.control_task is not None
                or not isinstance(output, (CommandCompleted, CommandFailed))
                or output.command.id != request["command_id"]):
            return
        if isinstance(output, CommandFailed) or output.command.status != "completed":
            self.control_request = None
            return
        # Never wait here: the controller still needs this worker output to
        # deliver the delegated result whose audible completion we will await.
        self.control_task = asyncio.create_task(self._apply_control(request))

    async def _apply_control(self, request):
        try:
            async with self.lock:
                if self.restarting:
                    raise RuntimeError("The server is restarting")
                if request["action"] == "start":
                    if not self.controller.active:
                        await self.transport.send_primary(RealtimeControlRequested(action="start"))
                elif request["call_id"] == self.call_id and self.controller.active:
                    await self.controller.finish_call(
                        activation_timeout=5 if request["command_id"].startswith("realtime-delegation:") else 0,
                    )
                    self.call_id = None
                    await self.runtime.publish(RealtimeControlRequested(
                        action="stop", call_id=request["call_id"],
                    ))
        except Exception as error:
            logger.exception("Voice control request failed")
            await self.runtime.publish(RealtimeControlFailed(action=request["action"], error=str(error)))
        finally:
            self.control_request = None
            self.control_task = None

    async def shutdown(self):
        """Discard ephemeral requests and close the controller on service teardown."""
        task = self.control_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.control_request = None
        await self.stop()

    async def prepare_restart(self, intent):
        """Finish audible output and persist the call boundary before server exit."""
        async with self.lock:
            self.restarting = True
            if not self.controller.active:
                return
            client_call_id = self.call_id
            try:
                await self.controller.finish_call(
                    activation_timeout=5 if intent.get("resume_route") == "realtime_delegation" else 0,
                )
            except BaseException:
                self.restarting = False
                raise
            self.call_id = None
            if client_call_id is not None:
                await self.runtime.publish(RealtimeRestarting(call_id=client_call_id))

    @endpoint("post", "/api/v1/realtime/calls/current/suspend",
              request=CALL_REQUEST, response={"type": "object"},
              authorization={"scope": "realtime:write"})
    async def suspend(self, call_id: str):
        """Drain this client's voice call before its interface reloads."""
        async with self.lock:
            if call_id == self.call_id and self.controller.active:
                await self.controller.finish_call()
                self.call_id = None
        return {"status": "ready"}

    async def update_plugin_binding(self, name, enabled, *, call=None):
        """Replace live context after a binding change; never reuse stale inputs."""
        async with self.lock:
            client_call_id = self.call_id
            transport_call_id = self.controller.call_id if client_call_id is not None else None
            if self.controller.active:
                # An agent tool may be waiting for this very operation. Reconcile
                # its final voice turns after returning the application result.
                await self.controller.stop(drain=False)
                self.call_id = None
            changed = False
            try:
                await self.runtime.set_plugin_binding(name, enabled, call=call)
                changed = True
            finally:
                if client_call_id is not None and not (changed and name == "realtime" and not enabled):
                    task = asyncio.create_task(self._request_reconnect(client_call_id, transport_call_id))
                    self._reconnect_tasks.add(task)
                    task.add_done_callback(self._reconnect_tasks.discard)

    async def _request_reconnect(self, client_call_id, transport_call_id):
        error = None
        try:
            await self.runtime.apply_event("realtime_barrier", {"call_id": transport_call_id}, timeout=None)
        except Exception:
            logger.exception("Could not reconcile voice before reconnecting")
            error = "Voice could not be reconciled; start a new call when the agent is ready."
        await self.runtime.publish(RealtimeReconnectRequested(call_id=client_call_id, error=error))

    @endpoint("post", "/api/v1/realtime/calls", request={
        **CALL_REQUEST, "properties": {**CALL_REQUEST["properties"],
                                      "offer_sdp": {"type": "string", "minLength": 1}},
        "required": ["call_id", "offer_sdp"],
    }, response={"type": "object"}, authorization={"scope": "realtime:write"})
    async def start(self, offer_sdp: str, call_id: str):
        """Join the canonical conversation after any active text turn finishes."""
        async with self.lock:
            if self.restarting:
                raise HttpError(503, "server_restarting", "The server is restarting")
            if self.controller.active:
                raise HttpError(409, "voice_active", "A voice call is already active")
            status = await self.runtime.request(StatusRequest(id=timestamp_id()))
            if status.foreground_active or status.foreground_pending:
                raise HttpError(409, "text_turn_active", "Finish or interrupt the current text turn before starting voice")
            snapshot = await self.runtime.request(SessionSnapshotRequest(id=timestamp_id()))
            try:
                result = await self.controller.start(offer_sdp, snapshot)
                self.call_id = call_id
                return result
            except RuntimeError as error:
                raise HttpError(409, "voice_unavailable", str(error)) from error

    @endpoint("post", "/api/v1/realtime/calls/current/ready",
              request=CALL_REQUEST,
              response={"type": "object"}, authorization={"scope": "realtime:write"})
    async def ready(self, call_id: str):
        """Allow context delivery after the browser audio peer is connected."""
        async with self.lock:
            if call_id != self.call_id:
                raise HttpError(409, "voice_changed", "This voice call is no longer current")
            try:
                return await self.controller.ready()
            except RuntimeError as error:
                raise HttpError(409, "voice_not_ready", str(error)) from error

    @endpoint("delete", "/api/v1/realtime/calls/current", status_code=204,
              request=CALL_REQUEST,
              authorization={"scope": "realtime:write"})
    async def stop(self, call_id: str | None = None):
        """Reconcile voice turns before returning to text conversation."""
        async with self.lock:
            if call_id is not None and call_id != self.call_id:
                return
            if call_id is None:
                tasks = tuple(self._reconnect_tasks)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._reconnect_tasks.clear()
            await self.controller.stop()
            self.call_id = None
