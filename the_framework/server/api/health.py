"""Declarative health endpoint for a composed agent application."""

import inspect

from .declarations import endpoint


class ApplicationHealthApi:
    """Project lifecycle health from the application's service graph."""

    def __init__(self, projection=None, *, runtime=None):
        self.projection = projection
        self.runtime = runtime

    @endpoint(
        "get", "/api/v1/health",
        response={"type": "object"},
        authorization={"scope": "health:read"},
        context="call",
    )
    async def health(self, call):
        """Return aggregate and per-service lifecycle health."""
        graph = getattr(self.runtime, "service_graph", None) or call.application.service_graph
        services = await graph.health()
        degraded = any(
            item["critical"] and item["status"] != "running"
            for item in services.values()
        )
        payload = {
            "status": "degraded" if degraded else "ok",
            "services": services,
        }
        if self.runtime is not None:
            diagnostics = self.runtime.health()
            if inspect.isawaitable(diagnostics):
                diagnostics = await diagnostics
            payload.update({key: value for key, value in diagnostics.items() if key != "status"})
            if diagnostics.get("status") != "ok":
                payload["status"] = "degraded"
        if self.projection is None:
            return payload
        result = self.projection(payload)
        return await result if inspect.isawaitable(result) else result


__all__ = ["ApplicationHealthApi"]
