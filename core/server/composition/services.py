"""Dependency-ordered lifecycle for application server services."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from modict import modict

from .dependencies import dependency_order

Lifecycle = Callable[[Any, "ServiceContext"], object]
HealthProbe = Callable[[Any, "ServiceContext"], object]


async def invoke_lifecycle(spec, action, context):
    """Invoke an explicit adapter or the service's conventional lifecycle method."""
    callback = getattr(spec, action)
    if callback is not None:
        result = callback(spec.service, context)
    else:
        method = getattr(spec.service, action, None)
        if method is None:
            return None
        result = method()
    return await result if inspect.isawaitable(result) else result


class ServiceSpec(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    service: object
    depends_on: tuple[str, ...] = ()
    start: Lifecycle | None = None
    stop: Lifecycle | None = None
    health: HealthProbe | None = None
    critical: bool = True

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(
                "service name must contain only letters, numbers, and underscores"
            )
        if self.name in self.depends_on:
            raise ValueError(f"service cannot depend on itself: {self.name}")


class ServiceContext:
    """Typed-by-name runtime lookup shared only by process-local services."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def add(self, name, value):
        if name in self._values:
            if self._values[name] is value:
                return value
            raise ValueError(f"duplicate service context value: {name}")
        self._values[name] = value
        return value

    def get(self, name, default=None):
        return self._values.get(name, default)

    def require(self, name):
        try:
            return self._values[name]
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
        return self.require(name)

    def __contains__(self, name):
        return name in self._values

    def snapshot(self):
        return dict(self._values)


class ServiceGraph:
    """Start services topologically and stop them in exact reverse order."""

    def __init__(self, specs=(), *, context=None):
        self.context = context or ServiceContext()
        self.specs = {}
        self.order = []
        self.started = []
        self.errors = {}
        for spec in specs:
            self.add(spec)

    @property
    def running(self):
        return bool(self.started)

    def add(self, spec):
        if not isinstance(spec, ServiceSpec):
            raise TypeError("application services must be ServiceSpec instances")
        if self.started:
            raise RuntimeError("cannot mutate a running service graph")
        if spec.name in self.specs:
            raise ValueError(f"duplicate application service: {spec.name}")
        self.specs[spec.name] = spec
        self.order = []
        return spec.service

    def resolve_order(self):
        if self.order:
            return list(self.order)
        self.order = dependency_order(
            {name: spec.depends_on for name, spec in self.specs.items()},
            kind="application service",
        )
        return list(self.order)

    async def start(self):
        if self.started:
            return self.context
        self.errors.clear()
        try:
            for name in self.resolve_order():
                spec = self.specs[name]
                unavailable = [
                    dependency
                    for dependency in spec.depends_on
                    if dependency not in self.started
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
        except Exception:
            await self.stop()
            raise
        return self.context

    async def stop(self):
        failures = []
        for name in reversed(self.started):
            try:
                await invoke_lifecycle(self.specs[name], "stop", self.context)
            except Exception as error:
                self.errors[name] = str(error)
                failures.append(error)
            finally:
                self.context.remove(name, self.specs[name].service)
        self.started.clear()
        if failures:
            raise ExceptionGroup("application service shutdown failed", failures)

    async def health(self):
        result = {}
        started = set(self.started)
        for name in self.resolve_order():
            spec = self.specs[name]
            payload = None
            try:
                payload = await invoke_lifecycle(spec, "health", self.context)
            except Exception as error:
                payload = {"status": "error", "error": str(error)}
            result[name] = {
                "status": (
                    "error" if name in self.errors
                    else "running" if name in started
                    else "stopped"
                ),
                "critical": spec.critical,
                **({"details": payload} if payload is not None else {}),
            }
        return result
