"""Reusable declarative HTTP API for one canonical agent runtime."""

from fastapi.responses import JSONResponse

from ..runtime.session import InvalidWorkerResult

from .declarations import endpoint
from ...agent.runtime.protocol import (
    CompactRequest,
    ConfigSnapshotRequest,
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
        self, runtime, *, session_projection="display",
    ):
        self.runtime = runtime
        self.session_projection = session_projection

    @endpoint("post", "/api/v1/server/prepare-restart",
              response={"type": "object"}, authorization={"scope": "server:write"})
    async def prepare_restart(self):
        """Drain conversation services before an authenticated supervisor restart."""
        await self.runtime.prepare_restart()
        return {"status": "ready"}

    @endpoint(
        "get", "/api/v1/status",
        response={"type": "object"},
        authorization={"scope": "agent:read"},
    )
    async def status(self):
        """Return current worker, queue, context, and quota status."""
        try:
            status = (await self.runtime.request(StatusRequest(id=timestamp_id()))).copy()
            status["server"] = self.runtime.server_status()
            return status
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
        response={"type": "object"},
        authorization={"scope": "session:write"},
    )
    async def reset_session(self):
        """Atomically reset canonical session state through application policy."""
        try:
            return await self.runtime.reset_session()
        except NotImplementedError as cause:
            raise HttpError(
                501, "session_reset_unavailable",
                "Canonical session reset is not configured",
            ) from cause
        except RuntimeError as cause:
            raise HttpError(500, "session_reset_failed", str(cause)) from cause

    @endpoint(
        "get", "/api/v1/config",
        response={"type": "object"},
        authorization={"scope": "config:read"},
    )
    async def config(self):
        """Return canonical agent and plugin configuration."""
        return await self.runtime.request(ConfigSnapshotRequest(id=timestamp_id()))

    @endpoint(
        "get", "/api/v1/state",
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
        command_id = timestamp_id()
        try:
            result = await self.runtime.update_config(updates, id=command_id)
        except InvalidWorkerResult as cause:
            raise HttpError(502, "invalid_agent_result", str(cause)) from cause
        return {
            "id": command_id,
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
