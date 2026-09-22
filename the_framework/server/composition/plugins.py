"""Transactional server runtime and agent-binding state for declared plugins."""

from __future__ import annotations

import inspect
from modict import modict

from fastapi import FastAPI

from ...utils.persistence import MappingStore

from ..api.endpoints import EndpointRegistry
from ..api.websockets import WebSocketRegistry
from .dependencies import dependency_order
from .services import invoke_lifecycle


class EndpointSnapshotRouter:
    """One stable ASGI mount delegating to an atomically replaced application."""

    def __init__(self, on_swap=None):
        self.snapshot = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.on_swap = on_swap

    def swap(self, snapshot):
        previous = self.snapshot
        self.snapshot = snapshot
        if self.on_swap is not None:
            self.on_swap(snapshot)
        return previous

    async def __call__(self, scope, receive, send):
        await self.snapshot(scope, receive, send)


class PluginStatus(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    name: str
    installed: bool
    loaded: bool
    running: bool
    binding_enabled: bool
    runtime_required: bool
    binding_required: bool


class PluginHost:
    """Own loaded/running runtime state independently from agent bindings."""

    def __init__(
        self,
        plan,
        application_context,
        *,
        security=None,
        validate_responses=True,
        state_path=None,
        binding_update=None,
        runtime_update=None,
        occupied=(),
        reserved_prefixes=(),
        openapi_changed=None,
    ):
        self.plan = plan
        self.application_context = application_context
        self.security = security
        self.validate_responses = validate_responses
        self.binding_update = binding_update
        self.runtime_update = runtime_update
        self.occupied = frozenset(occupied)
        self.reserved_prefixes = tuple(reserved_prefixes)
        self.router = EndpointSnapshotRouter(openapi_changed)
        self.store = (
            MappingStore(state_path, field="plugins")
            if state_path is not None
            else None
        )
        persisted = self.store.load() if self.store is not None else {}
        self.states = {}
        for name, spec in plan.plugins.items():
            saved = persisted.get(name, {})
            running = bool(saved.get("running", spec.runtime_enabled))
            binding = bool(
                spec.agent is not None
                and saved.get("binding_enabled", spec.binding_enabled)
            )
            if not running:
                binding = False
            if spec.runtime_required:
                running = True
            if spec.binding_required:
                binding = True
            self.states[name] = {
                "loaded": True,
                "running": running,
                "binding_enabled": binding,
            }
        exports = {
            capability.name: owner
            for owner, spec in plan.plugins.items()
            for capability in spec.capabilities
        }
        order = dependency_order({
            name: tuple(exports[requirement.name] for requirement in spec.requires)
            for name, spec in plan.plugins.items()
        }, kind="plugin")
        for name in order:
            spec = plan.plugins[name]
            if not self.states[name]["running"]:
                continue
            for requirement in spec.requires:
                owner = exports[requirement.name]
                if not self.states[owner]["running"]:
                    if spec.runtime_required:
                        raise ValueError(
                            f"required plugin {name} requires stopped capability: "
                            f"{requirement.name}"
                        )
                    self.states[name]["running"] = False
                    self.states[name]["binding_enabled"] = False
                    break
        self.started = []
        self.router.swap(self._build_snapshot(
            name for name, state in self.states.items() if state["running"]
        ))

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
                runtime_required=spec.runtime_required,
                binding_required=spec.binding_required,
            ))
        return result[0] if name is not None else result

    def _save(self):
        if self.store is not None:
            self.store.save(self.states)

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

    async def _start_extensions(self, name):
        started = []
        try:
            for extension in self.plan.plugin_extensions.get(name, ()):
                if extension.service is None:
                    continue
                unavailable = [
                    dependency for dependency in extension.requires
                    if dependency not in self.application_context.services
                ]
                if unavailable:
                    raise RuntimeError(
                        f"plugin {name} dependencies are unavailable: "
                        + ", ".join(unavailable)
                    )
                await invoke_lifecycle(extension, "start", self.application_context.services)
                self.application_context.services.add(extension.name, extension.service)
                started.append(extension)
        except Exception:
            for extension in reversed(started):
                try:
                    await invoke_lifecycle(extension, "stop", self.application_context.services)
                finally:
                    self.application_context.services.remove(extension.name, extension.service)
            raise

    async def _stop_extensions(self, name):
        failures = []
        for extension in reversed(self.plan.plugin_extensions.get(name, ())):
            if extension.service is None:
                continue
            try:
                await invoke_lifecycle(extension, "stop", self.application_context.services)
            except Exception as error:
                failures.append(error)
            finally:
                self.application_context.services.remove(extension.name, extension.service)
        if failures:
            raise ExceptionGroup(f"plugin {name} runtime shutdown failed", failures)

    async def start(self):
        running = [
            name for name, state in self.states.items() if state["running"]
        ]
        snapshot = self._build_snapshot(running)
        try:
            for name in self._runtime_order(running):
                await self._start_extensions(name)
                self.started.append(name)
            if self.runtime_update is not None:
                for name, state in self.states.items():
                    result = self.runtime_update(name, state["running"])
                    if inspect.isawaitable(result):
                        await result
        except Exception:
            await self.stop()
            raise
        self.router.swap(snapshot)
        self._save()
        return self

    async def stop(self):
        failures = []
        for name in reversed(self.started):
            try:
                await self._stop_extensions(name)
            except Exception as error:
                failures.append(error)
        self.started.clear()
        self.router.swap(self._build_snapshot(()))
        if failures:
            raise ExceptionGroup("plugin runtime shutdown failed", failures)

    async def set_binding(self, name, enabled):
        spec = self.plan.plugins.get(name)
        if spec is None:
            raise ValueError(f"unknown plugin: {name}")
        state = self.states[name]
        enabled = bool(enabled)
        if enabled and spec.agent is None:
            raise RuntimeError(f"plugin has no agent binding: {name}")
        if enabled and not state["running"]:
            raise RuntimeError(f"plugin runtime is stopped: {name}")
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

    async def stop_runtime(self, name):
        spec = self.plan.plugins.get(name)
        if spec is None:
            raise ValueError(f"unknown plugin: {name}")
        if spec.runtime_required:
            raise RuntimeError(f"plugin runtime is required: {name}")
        exported = {capability.name for capability in spec.capabilities}
        dependents = [
            candidate_name
            for candidate_name, candidate in self.plan.plugins.items()
            if self.states[candidate_name]["running"]
            and candidate_name != name
            and any(requirement.name in exported for requirement in candidate.requires)
        ]
        if dependents:
            raise RuntimeError(
                f"plugin runtime is required by: {', '.join(sorted(dependents))}"
            )
        state = self.states[name]
        if not state["running"]:
            return self.status(name)
        previous_binding = state["binding_enabled"]
        if previous_binding:
            if self.binding_update is not None:
                result = self.binding_update(name, False)
                if inspect.isawaitable(result):
                    await result
            state["binding_enabled"] = False
        try:
            if self.runtime_update is not None:
                result = self.runtime_update(name, False)
                if inspect.isawaitable(result):
                    await result
            await self._stop_extensions(name)
            running = [
                item for item, value in self.states.items()
                if value["running"] and item != name
            ]
            snapshot = self._build_snapshot(running)
            state["running"] = False
            state["binding_enabled"] = False
            self._save()
        except Exception:
            state["running"] = True
            state["binding_enabled"] = previous_binding
            await self._start_extensions(name)
            if self.runtime_update is not None:
                result = self.runtime_update(name, True)
                if inspect.isawaitable(result):
                    await result
            if previous_binding and self.binding_update is not None:
                result = self.binding_update(name, True)
                if inspect.isawaitable(result):
                    await result
            raise
        self.router.swap(snapshot)
        if name in self.started:
            self.started.remove(name)
        return self.status(name)

    async def start_runtime(self, name):
        spec = self.plan.plugins.get(name)
        if spec is None:
            raise ValueError(f"unknown plugin: {name}")
        state = self.states[name]
        if state["running"]:
            return self.status(name)
        for requirement in spec.requires:
            owner = next((
                item for item, candidate in self.plan.plugins.items()
                if any(capability.name == requirement.name
                       and capability.version >= requirement.min_version
                       for capability in candidate.capabilities)
            ), None)
            if owner is None or not self.states[owner]["running"]:
                raise RuntimeError(
                    f"plugin capability is unavailable: {requirement.name}"
                )
        running = [
            item for item, value in self.states.items() if value["running"]
        ]
        snapshot = self._build_snapshot((*running, name))
        try:
            await self._start_extensions(name)
            if self.runtime_update is not None:
                result = self.runtime_update(name, True)
                if inspect.isawaitable(result):
                    await result
            state["running"] = True
            state["binding_enabled"] = False
            self._save()
        except Exception:
            state["running"] = False
            state["binding_enabled"] = False
            if self.runtime_update is not None:
                result = self.runtime_update(name, False)
                if inspect.isawaitable(result):
                    await result
            await self._stop_extensions(name)
            raise
        self.router.swap(snapshot)
        self.started.append(name)
        return self.status(name)


__all__ = ["EndpointSnapshotRouter", "PluginHost", "PluginStatus"]
