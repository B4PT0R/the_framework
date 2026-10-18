"""Startup-installed plugin runtimes and persistent agent bindings."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from modict import modict

from .services import ServiceGraph


class PluginStatus(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    name: str
    installed: bool
    loaded: bool
    running: bool
    binding_available: bool
    binding_enabled: bool
    binding_required: bool


class PluginHost:
    """Hold startup-fixed plugin status and persistent live agent bindings."""

    def __init__(
        self,
        plan,
        *,
        service_graph: ServiceGraph,
        service_owners: Mapping[str, str],
        binding_update=None,
        binding_snapshot=None,
    ):
        self.plan = plan
        self.service_graph = service_graph
        self.service_owners = service_owners
        self.binding_update = binding_update
        self.binding_snapshot = binding_snapshot
        self._binding_lock = asyncio.Lock()
        self.bindings = {
            name: bool(spec.agent_extension is not None and spec.agent_binding_enabled)
            for name, spec in plan.plugins.items()
        }

    def status(self, name=None):
        names = (name,) if name is not None else tuple(self.plan.plugins)
        result = []
        for plugin_name in names:
            if plugin_name not in self.plan.plugins:
                raise ValueError(f"unknown plugin: {plugin_name}")
            spec = self.plan.plugins[plugin_name]
            result.append(PluginStatus(
                name=plugin_name,
                installed=True,
                loaded=True,
                running=all(
                    name in self.service_graph.context
                    for name, owner in self.service_owners.items()
                    if owner == plugin_name
                ),
                binding_available=spec.agent_extension is not None,
                binding_enabled=self.bindings[plugin_name],
                binding_required=spec.agent_binding_required,
            ))
        return result[0] if name is not None else result

    async def start(self):
        snapshot = self.binding_snapshot() if self.binding_snapshot is not None else {}
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        if not isinstance(snapshot, Mapping):
            raise TypeError("plugin binding snapshot must be a mapping")
        bindings = self.bindings.copy()
        for name, enabled in snapshot.items():
            spec = self.plan.plugins.get(name)
            if spec is None or spec.agent_extension is None:
                continue
            if not isinstance(enabled, bool):
                raise TypeError(f"worker plugin binding must be boolean: {name}")
            if spec.agent_binding_required and not enabled:
                raise RuntimeError(f"required plugin binding is disabled: {name}")
            bindings[name] = enabled
        self.bindings = bindings
        return self

    async def set_binding(self, name, enabled, *, call=None):
        async with self._binding_lock:
            return await self._set_binding(name, enabled, call=call)

    async def _set_binding(self, name, enabled, *, call=None):
        spec = self.plan.plugins.get(name)
        if spec is None:
            raise ValueError(f"unknown plugin: {name}")
        enabled = bool(enabled)
        if enabled and spec.agent_extension is None:
            raise RuntimeError(f"plugin has no agent binding: {name}")
        if not enabled and spec.agent_binding_required:
            raise RuntimeError(f"plugin binding is required: {name}")
        if self.bindings[name] == enabled:
            return self.status(name)
        if self.binding_update is not None:
            result = self.binding_update(name, enabled, call=call)
            if inspect.isawaitable(result):
                await result
        self.bindings[name] = enabled
        return self.status(name)

__all__ = ["PluginHost", "PluginStatus"]
