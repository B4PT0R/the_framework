"""Reusable declarative HTTP API for one canonical agent runtime."""

import inspect

from fastapi.responses import JSONResponse

from ...agent import endpoint
from ...agent.runtime.protocol import (
    AgentConfigUpdated,
    CompactRequest,
    ConfigSnapshotRequest,
    ConfigUpdateRequest,
    InterruptRequest,
    SessionPageRequest,
    StateSnapshotRequest,
    StatusRequest,
)
from the_framework.utils.ids import timestamp_id

from .endpoints import HttpError


class CanonicalAgentApi:
    """Transport-neutral operations common to agent applications."""

    def __init__(
        self, runtime, *, session_projection="full", reset_handler=None,
    ):
        self.runtime = runtime
        self.session_projection = session_projection
        self.reset_handler = reset_handler

    @endpoint(
        "get", "/api/v1/status",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "agent:read"},
    )
    async def status(self):
        """Return current worker, queue, context, and quota status."""
        try:
            return await self.runtime.request(StatusRequest(id=timestamp_id()))
        except TimeoutError:
            # Preserve the established client contract for this transient
            # status probe while other controlled endpoint failures use the
            # richer framework error envelope.
            return JSONResponse(
                {"error": "agent status temporarily unavailable"},
                status_code=503,
            )

    @endpoint(
        "post", "/api/v1/agent/compact",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "agent:write"},
        status_code=202,
    )
    async def compact(self):
        """Submit a sequential canonical-session compaction."""
        command = CompactRequest(id=timestamp_id(), priority=5)
        await self.runtime.submit(command)
        return {"id": command.id, "status": "submitted"}

    @endpoint(
        "get", "/api/v1/session",
        request={
            "type": "object",
            "properties": {
                "before": {"type": ["string", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "session:read"},
    )
    async def session(self, before=None, limit=25):
        """Return a page of canonical archived conversation turns."""
        return await self.runtime.request(SessionPageRequest(
            id=timestamp_id(),
            before=before,
            limit=limit,
            projection=self.session_projection,
        ), timeout=15)

    @endpoint(
        "delete", "/api/v1/session",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "session:write"},
    )
    async def reset_session(self):
        """Atomically reset canonical session state through application policy."""
        if self.reset_handler is None:
            raise HttpError(
                501,
                "session_reset_unavailable",
                "Canonical session reset is not configured",
            )
        try:
            result = self.reset_handler()
            return await result if inspect.isawaitable(result) else result
        except RuntimeError as cause:
            raise HttpError(500, "session_reset_failed", str(cause)) from cause

    @endpoint(
        "get", "/api/v1/config",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "config:read"},
    )
    async def config(self):
        """Return canonical agent and plugin configuration."""
        return await self.runtime.request(ConfigSnapshotRequest(id=timestamp_id()))

    @endpoint(
        "get", "/api/v1/state",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "state:read"},
    )
    async def state(self):
        """Return current server-owned plugin state projections."""
        return await self.runtime.request(StateSnapshotRequest(id=timestamp_id()))

    @endpoint(
        "patch", "/api/v1/config",
        request={"type": "object", "minProperties": 1},
        response={"type": "object"},
        authorization={"scope": "config:write"},
    )
    async def update_config(self, **updates):
        """Validate and atomically persist an agent configuration update."""
        command = ConfigUpdateRequest(id=timestamp_id(), updates=updates)
        result = await self.runtime.command(command)
        if not isinstance(result, AgentConfigUpdated):
            raise HttpError(
                502,
                "invalid_agent_result",
                "Agent completed the config update without a result",
            )
        return {
            "id": command.id,
            "status": "applied",
            "config": result["config"],
            "restart_required": result.restart_required,
        }

    @endpoint(
        "post", "/api/v1/agent/interrupt",
        request={
            "type": "object",
            "properties": {"reason": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "agent:write"},
        status_code=202,
    )
    async def interrupt(self, reason=None):
        """Interrupt active agent work without blocking the HTTP request."""
        command = InterruptRequest(id=timestamp_id(), reason=reason)
        await self.runtime.submit(command)
        return {"id": command.id, "status": "submitted"}


__all__ = ["CanonicalAgentApi"]
