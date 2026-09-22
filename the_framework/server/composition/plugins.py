"""Startup-installed plugin runtimes and persistent agent bindings."""

from __future__ import annotations

import inspect
from modict import modict

from fastapi import FastAPI

from ...utils.persistence import MappingStore

from ..api.endpoints import EndpointRegistry
from ..api.websockets import WebSocketRegistry
from .dependencies import dependency_order


class PluginStatus(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    name: str
    installed: bool
    loaded: bool
    running: bool
    binding_enabled: bool
    binding_required: bool


class PluginHost:
    """Hold startup-fixed plugin routes and persistent live agent bindings."""

    def __init__(
        self,
        plan,
        application_context,
        *,
        security=None,
        validate_responses=True,
        state_path=None,
        binding_update=None,
        occupied=(),
        reserved_prefixes=(),
    ):
        self.plan = plan
        self.application_context = application_context
        self.security = security
        self.validate_responses = validate_responses
        self.binding_update = binding_update
        self.occupied = frozenset(occupied)
        self.reserved_prefixes = tuple(reserved_prefixes)
        self.store = (
            MappingStore(state_path, field="plugins")
            if state_path is not None
            else None
        )
        persisted = self.store.load() if self.store is not None else {}
        self.states = {}
        for name, spec in plan.plugins.items():
            saved = persisted.get(name, {})
            if saved.get("running") is False:
                raise ValueError(
                    f"legacy plugin state has a stopped runtime: {name}; "
                    "remove this plugin from the startup declaration, then "
                    "remove its obsolete running field from plugin state"
                )
            binding = bool(
                spec.agent is not None
                and saved.get("binding_enabled", spec.binding_enabled)
            )
            if spec.binding_required:
                binding = True
            self.states[name] = {
                "loaded": True,
                "running": True,
                "binding_enabled": binding,
            }
        self.router = self._build_snapshot(self.plan.plugins)

    def status(self, name=None):
        names = (name,) if name is not None else tuple(self.plan.plugins)
        result = []
        for plugin_name in names:
            if plugin_name not in self.plan.plugins:
                raise ValueError(f"unknown plugin: {plugin_name}")
            spec = self.plan.plugins[plugin_name]
            state = self.states[plugin_name]
            result.append(PluginStatus(
                name=plugin_name,
                installed=True,
                loaded=state["loaded"],
                running=state["running"],
                binding_enabled=state["binding_enabled"],
                binding_required=spec.binding_required,
            ))
        return result[0] if name is not None else result

    def _save(self):
        if self.store is not None:
            self.store.save({
                name: {"binding_enabled": state["binding_enabled"]}
                for name, state in self.states.items()
            })

    def _runtime_order(self, names):
        names = tuple(names)
        selected = set(names)
        exports = {
            capability.name: name
            for name, spec in self.plan.plugins.items()
            for capability in spec.capabilities
        }
        return dependency_order({
            name: tuple(
                exports[requirement.name]
                for requirement in self.plan.plugins[name].requires
                if exports.get(requirement.name) in selected
            )
            for name in names
        }, kind="plugin")

    def _build_snapshot(self, running):
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        registry = EndpointRegistry(
            app,
            security=self.security,
            validate_responses=self.validate_responses,
            application_context=self.application_context,
            occupied=self.occupied,
            reserved_prefixes=self.reserved_prefixes,
        )
        sockets = WebSocketRegistry(app, security=self.security)
        from .application import _declared_endpoints

        for name in self._runtime_order(running):
            for extension in self.plan.plugin_extensions.get(name, ()):
                for source in extension.endpoints:
                    for endpoint in _declared_endpoints(source):
                        registry.add(endpoint, owner=name)
                for endpoint in extension.websockets:
                    if any(
                        endpoint.path == prefix
                        or endpoint.path.startswith(f"{prefix.rstrip('/')}/")
                        for prefix in self.reserved_prefixes
                    ):
                        raise ValueError(
                            "plugin WebSocket is shadowed by a mount: "
                            f"{endpoint.path}"
                        )
                    sockets.add(endpoint, owner=name)
        registry.install()
        sockets.install()
        return app

    async def start(self):
        self._save()
        return self

    async def set_binding(self, name, enabled):
        spec = self.plan.plugins.get(name)
        if spec is None:
            raise ValueError(f"unknown plugin: {name}")
        state = self.states[name]
        enabled = bool(enabled)
        if enabled and spec.agent is None:
            raise RuntimeError(f"plugin has no agent binding: {name}")
        if not enabled and spec.binding_required:
            raise RuntimeError(f"plugin binding is required: {name}")
        if state["binding_enabled"] == enabled:
            return self.status(name)
        previous = state["binding_enabled"]
        if self.binding_update is not None:
            result = self.binding_update(name, enabled)
            if inspect.isawaitable(result):
                await result
        state["binding_enabled"] = enabled
        try:
            self._save()
        except Exception:
            state["binding_enabled"] = previous
            if self.binding_update is not None:
                result = self.binding_update(name, previous)
                if inspect.isawaitable(result):
                    await result
            raise
        return self.status(name)

__all__ = ["PluginHost", "PluginStatus"]
