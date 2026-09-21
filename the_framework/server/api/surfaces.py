"""Authenticated build and release controls for client surfaces."""

from ...agent import endpoint

from .endpoints import HttpError


class SurfaceApi:
    def __init__(self, application_context):
        self.application_context = application_context

    @property
    def service(self):
        return self.application_context.surface_service

    @endpoint(
        "get", "/api/v1/surfaces",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        authorization={"scope": "surfaces:read"},
    )
    def status(self):
        """Return active, previous and candidate releases."""
        return {
            "surfaces": [{
                "name": name,
                **self.service.state.get(name, {}),
                "candidate": self.service.candidates.get(name),
                "preview_route": surface.preview_route,
                "routes": list(surface.routes),
            } for name, surface in self.service.surfaces.items()]
        }

    @endpoint(
        "post", "/api/v1/surfaces/{name}/build",
        request={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "surfaces:write"},
    )
    async def build(self, name):
        """Build one immutable preview candidate."""
        try:
            release = await self.service.build(name)
        except ValueError as error:
            raise HttpError(404, "surface_not_found", "Surface not found") from error
        except RuntimeError as error:
            raise HttpError(409, "surface_build_failed", "Surface build failed") from error
        surface = self.service.surfaces[name]
        return {
            **release,
            "preview": f"{surface.preview_route}/{release.release}/",
        }

    @endpoint(
        "post", "/api/v1/surfaces/{name}/cancel",
        request={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "surfaces:write"},
    )
    async def cancel(self, name):
        """Cancel a running build and its complete subprocess group."""
        if name not in self.service.surfaces:
            raise HttpError(404, "surface_not_found", "Surface not found")
        return {"surface": name, "cancelled": await self.service.cancel(name)}

    @endpoint(
        "post", "/api/v1/surfaces/{name}/publish",
        request={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "release": {"type": ["string", "null"]},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "surfaces:write"},
    )
    async def publish(self, name, release=None):
        """Atomically publish a verified candidate and notify clients."""
        try:
            return await self.service.publish(name, release)
        except (LookupError, ValueError) as error:
            raise HttpError(404, "surface_release_not_found", "Release not found") from error

    @endpoint(
        "post", "/api/v1/surfaces/{name}/rollback",
        request={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "surfaces:write"},
    )
    async def rollback(self, name):
        """Restore the previous release without rebuilding."""
        try:
            return await self.service.rollback(name)
        except (KeyError, RuntimeError) as error:
            raise HttpError(
                409,
                "surface_rollback_unavailable",
                "Surface rollback is unavailable",
            ) from error


__all__ = ["SurfaceApi"]
