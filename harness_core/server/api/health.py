"""Declarative health endpoint for a composed agent application."""

import inspect

from ...agent import endpoint


class ApplicationHealthApi:
    """Project lifecycle health from the application's service graph."""

    def __init__(self, projection=None):
        self.projection = projection

    @endpoint(
        "get", "/api/v1/health",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "health:read"},
        context="call",
    )
    async def health(self, call):
        """Return aggregate and per-service lifecycle health."""
        services = await call.application.service_graph.health()
        degraded = any(
            item["critical"] and item["status"] != "running"
            for item in services.values()
        )
        payload = {
            "status": "degraded" if degraded else "ok",
            "services": services,
        }
        if self.projection is None:
            return payload
        result = self.projection(payload)
        return await result if inspect.isawaitable(result) else result


__all__ = ["ApplicationHealthApi"]
