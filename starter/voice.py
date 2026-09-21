"""Local voice surface over the framework's canonical realtime controller."""

import asyncio

from the_framework import endpoint
from the_framework.agent.runtime.protocol import SessionSnapshotRequest, StatusRequest
from the_framework.utils.ids import timestamp_id
from the_framework.server.api.endpoints import HttpError

CALL_REQUEST = {"type": "object", "properties": {
    "call_id": {"type": "string", "minLength": 1, "maxLength": 200},
}, "required": ["call_id"], "additionalProperties": False}


class VoiceApi:
    def __init__(self, runtime, controller):
        self.runtime = runtime
        self.controller = controller
        self.lock = asyncio.Lock()
        self.call_id = None

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
            await self.controller.stop()
            self.call_id = None
