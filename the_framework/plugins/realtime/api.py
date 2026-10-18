"""Neutral voice surface over the framework's canonical realtime controller."""

import asyncio
import logging

from the_framework import endpoint
from the_framework.agent.runtime.protocol import SessionSnapshotRequest, StatusRequest
from the_framework.server.api.endpoints import HttpError
from the_framework.utils.ids import timestamp_id

from ...agent.models.base import TypedBase

CALL_REQUEST = {"type": "object", "properties": {
    "call_id": {"type": "string", "minLength": 1, "maxLength": 200},
}, "required": ["call_id"], "additionalProperties": False}


logger = logging.getLogger(__name__)


class RealtimeReconnectRequested(TypedBase):
    """Ask the owning client to renegotiate after canonical history has drained."""

    call_id: str
    error: str | None = None


class RealtimeApi:
    def __init__(self, runtime, controller):
        self.runtime = runtime
        self.controller = controller
        self.lock = asyncio.Lock()
        self.call_id = None
        self._reconnect_tasks = set()

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
