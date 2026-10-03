"""Startup-installed plugin runtimes and persistent agent bindings."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from modict import modict

from ...utils.persistence import MappingStore
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
        state_path=None,
        binding_update=None,
        binding_snapshot=None,
    ):
        self.plan = plan
        self.service_graph = service_graph
        self.service_owners = service_owners
        self.binding_update = binding_update
        self.binding_snapshot = binding_snapshot
        self._binding_lock = asyncio.Lock()
        self.store = (
            MappingStore(state_path, field="plugins")
            if state_path is not None
            else None
        )
        persisted = self.store.load() if self.store is not None else {}
        self._stored_bindings = set(persisted)
        self.bindings = {}
        for name, spec in plan.plugins.items():
            saved = persisted.get(name, {})
            if saved.get("running") is False:
                raise ValueError(
                    f"legacy plugin state has a stopped runtime: {name}; "
                    "remove this plugin from the startup declaration, then "
                    "remove its obsolete running field from plugin state"
                )
            binding = bool(
                spec.agent_extension is not None
                and saved.get("binding_enabled", spec.agent_binding_enabled)
            )
            if spec.agent_binding_required:
                binding = True
            self.bindings[name] = binding

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
                    if owner == plugin_name and name in self.service_graph.specs
                ),
                binding_available=spec.agent_extension is not None,
                binding_enabled=self.bindings[plugin_name],
                binding_required=spec.agent_binding_required,
            ))
        return result[0] if name is not None else result

    def _save(self):
        if self.store is not None:
            self.store.save({
                name: {"binding_enabled": enabled}
                for name, enabled in self.bindings.items()
            })

    async def start(self):
        snapshot = self.binding_snapshot() if self.binding_snapshot is not None else {}
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        if not isinstance(snapshot, Mapping):
            raise TypeError("plugin binding snapshot must be a mapping")
        previous = self.bindings.copy()
        try:
            for name, enabled in snapshot.items():
                spec = self.plan.plugins.get(name)
                if spec is None or spec.agent_extension is None:
                    continue
                if not isinstance(enabled, bool):
                    raise TypeError(f"worker plugin binding must be boolean: {name}")
                if name in self._stored_bindings or spec.agent_binding_required:
                    if enabled != self.bindings[name]:
                        raise RuntimeError(
                            f"worker binding differs from persisted plugin state: {name}"
                        )
                else:
                    self.bindings[name] = enabled
            self._save()
        except Exception:
            self.bindings.update(previous)
            raise
        self._stored_bindings.update(self.bindings)
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
        previous = self.bindings[name]
        if self.binding_update is not None:
            result = self.binding_update(name, enabled, call=call)
            if inspect.isawaitable(result):
                await result
        self.bindings[name] = enabled
        try:
            self._save()
        except Exception:
            self.bindings[name] = previous
            if self.binding_update is not None:
                result = self.binding_update(name, previous, call=call)
                if inspect.isawaitable(result):
                    await result
            raise
        return self.status(name)

__all__ = ["PluginHost", "PluginStatus"]
