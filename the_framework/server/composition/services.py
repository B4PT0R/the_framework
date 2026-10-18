"""Dependency-ordered lifecycle for application server services."""

from __future__ import annotations

import inspect
from types import MappingProxyType
from collections.abc import Mapping

from .dependencies import dependency_order


async def invoke_lifecycle(spec, action, context):
    """Invoke an explicit adapter or the service's conventional lifecycle method."""
    callback = getattr(spec, action)
    if callback is not None:
        result = callback(spec.service, context)
    else:
        method = getattr(spec.service, action, None)
        if method is None or getattr(method, "http_endpoint", None) is not None:
            return None
        result = method()
    return await result if inspect.isawaitable(result) else result


class ServiceContext(Mapping):
    """Started local services with read-only access to application-owned services."""

    def __init__(self, values=None, *, parent=None):
        self._values = dict(values or {})
        self.parent = parent

    def add(self, name, value):
        if name in self:
            if self[name] is value:
                return value
            raise ValueError(f"duplicate service context value: {name}")
        self._values[name] = value
        return value

    def require(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise LookupError(f"application service is unavailable: {name}") from error

    def remove(self, name, value=None):
        current = self._values.get(name)
        if current is None:
            return None
        if value is not None and current is not value:
            raise ValueError(f"application service context value changed: {name}")
        return self._values.pop(name)

    def __getitem__(self, name):
        if name in self._values:
            return self._values[name]
        if self.parent is not None:
            spec = self.parent.specs.get(name)
            if spec is not None and spec.scope == "application":
                return self.parent.context[name]
        raise KeyError(name)

    def __iter__(self):
        yield from self._values
        if self.parent is not None:
            yield from (name for name, spec in self.parent.specs.items()
                        if spec.scope == "application" and name in self.parent.context
                        and name not in self._values)

    def __len__(self):
        return sum(1 for _ in self)

    def snapshot(self):
        return dict(self)

    @property
    def view(self):
        """Live, read-only lookup of services that have successfully started."""
        return MappingProxyType(self)


class ServiceGraph:
    """Start services topologically and stop them in exact reverse order."""

    def __init__(self, extensions=(), *, context=None, dependencies=None, construction_errors=None):
        from .application import ServerExtension

        self.context = context if context is not None else ServiceContext()
        self.specs = {}
        self.order = []
        self.started = []
        self._construction_errors = dict(construction_errors or {})
        self.errors = dict(self._construction_errors)
        for extension in extensions:
            if not isinstance(extension, ServerExtension):
                raise TypeError("application services must be resolved ServerExtension declarations")
            if ((extension.service is None and extension.name not in self._construction_errors)
                    or extension.service_factory is not None):
                raise ValueError("service lifecycle requires a resolved service")
            if extension.name in self.specs:
                raise ValueError(f"duplicate application service: {extension.name}")
            self.specs[extension.name] = extension
        if not self._construction_errors.keys() <= self.specs.keys():
            raise ValueError("construction errors must belong to installed services")
        self.dependencies = (
            {name: tuple(values) for name, values in dependencies.items()}
            if dependencies is not None else {
                name: (
                    *extension.requires_services,
                    *(dependency for dependency in extension.optional_services if dependency in self.specs),
                )
                for name, extension in self.specs.items()
            }
        )
        if self.dependencies.keys() != self.specs.keys():
            raise ValueError("service dependencies must describe exactly the installed services")

    @property
    def running(self):
        return bool(self.started)

    def resolve_order(self):
        if self.order:
            return list(self.order)
        inherited = self.context.parent
        shared = {name for name, spec in inherited.specs.items() if spec.scope == "application"} if inherited else set()
        unknown = {name for values in self.dependencies.values() for name in values
                   if name not in self.specs and name not in shared}
        if unknown:
            raise ValueError("unknown application service dependencies: " + ", ".join(sorted(unknown)))
        self.order = dependency_order(
            {name: tuple(value for value in values if value in self.specs)
             for name, values in self.dependencies.items()},
            kind="application service",
        )
        return list(self.order)

    async def start(self):
        if self.started:
            return self.context
        self.errors.clear()
        self.errors.update(self._construction_errors)
        try:
            for name in self.resolve_order():
                spec = self.specs[name]
                if name in self._construction_errors:
                    if spec.critical:
                        raise RuntimeError(self._construction_errors[name])
                    continue
                unavailable = [
                    dependency
                    for dependency in self.dependencies[name]
                    if dependency not in self.context
                ]
                if unavailable:
                    error = RuntimeError(
                        "application service dependencies are unavailable: "
                        + ", ".join(unavailable)
                    )
                    self.errors[name] = str(error)
                    if spec.critical:
                        raise error
                    continue
                try:
                    await invoke_lifecycle(spec, "start", self.context)
                except Exception as error:
                    self.errors[name] = str(error)
                    if spec.critical:
                        raise
                else:
                    self.context.add(name, spec.service)
                    self.started.append(name)
        except BaseException as startup_error:
            try:
                await self.stop()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup(
                    "application service startup and cleanup failed",
                    [startup_error, cleanup_error],
                ) from None
            raise
        return self.context

    async def stop(self):
        failures = []
        for name in reversed(self.started):
            try:
                await invoke_lifecycle(self.specs[name], "stop", self.context)
            except BaseException as error:
                self.errors[name] = str(error)
                failures.append(error)
            finally:
                self.context.remove(name, self.specs[name].service)
        self.started.clear()
        if failures:
            raise BaseExceptionGroup("application service shutdown failed", failures)

    async def health(self, *, names=None):
        result = {}
        parent = self.context.parent
        if parent is not None:
            shared = {name for name, spec in parent.specs.items() if spec.scope == "application"}
            result.update(await parent.health(names=shared if names is None else shared.intersection(names)))
        started = set(self.started)
        for name in self.resolve_order():
            if names is not None and name not in names:
                continue
            spec = self.specs[name]
            payload = None
            try:
                payload = (
                    {"status": "error", "phase": "construction", "error": self._construction_errors[name]}
                    if name in self._construction_errors
                    else await invoke_lifecycle(spec, "health", self.context)
                )
            except Exception as error:
                payload = {"status": "error", "error": str(error)}
            probe_failed = (
                isinstance(payload, dict)
                and payload.get("status") in {"error", "degraded", "unhealthy"}
            )
            result[name] = {
                "status": (
                    "error" if name in self.errors
                    else "degraded" if name in started and probe_failed
                    else "running" if name in started
                    else "stopped"
                ),
                "critical": spec.critical,
                **({"details": payload} if payload is not None else {}),
            }
        return result
