"""Authenticated control surface for declared plugin runtime and bindings."""

from ...agent import endpoint

from .endpoints import HttpError


class PluginHostApi:
    def __init__(self, application_context):
        self.application_context = application_context

    @property
    def host(self):
        return self.application_context.plugin_host

    @endpoint(
        "get",
        "/api/v1/plugins",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={
            "type": "object",
            "properties": {"plugins": {"type": "array", "items": {"type": "object"}}},
            "required": ["plugins"],
            "additionalProperties": False,
        },
        authorization={"scope": "plugins:read"},
    )
    def list_plugins(self):
        """Return installed, loaded, running and binding state separately."""
        return {"plugins": self.host.status()}

    @endpoint(
        "put",
        "/api/v1/plugins/{name}/binding",
        request={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "enabled": {"type": "boolean"},
            },
            "required": ["name", "enabled"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "plugins:write"},
    )
    async def set_binding(self, name, enabled):
        """Enable or disable this plugin only for the canonical agent."""
        try:
            return await self.host.set_binding(name, enabled)
        except ValueError as error:
            raise HttpError(404, "plugin_not_found", "Plugin not found") from error
        except RuntimeError as error:
            raise HttpError(409, "plugin_transition_rejected", str(error)) from error

    @endpoint(
        "put",
        "/api/v1/plugins/{name}/runtime",
        request={
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "running": {"type": "boolean"},
            },
            "required": ["name", "running"],
            "additionalProperties": False,
        },
        response={"type": "object"},
        authorization={"scope": "plugins:write"},
    )
    async def set_runtime(self, name, running):
        """Start or fully stop one already installed plugin runtime."""
        try:
            transition = (
                self.host.start_runtime if running else self.host.stop_runtime
            )
            return await transition(name)
        except ValueError as error:
            raise HttpError(404, "plugin_not_found", "Plugin not found") from error
        except RuntimeError as error:
            raise HttpError(409, "plugin_transition_rejected", str(error)) from error


__all__ = ["PluginHostApi"]
