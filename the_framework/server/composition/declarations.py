"""Composition inputs and feature declarations, independent of server assembly."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from modict import modict

from ...agent.spec import AgentSpec
from ...agent.extensions.specialists import AgentTrigger
from .services import ServiceContext


def _tuple(value):
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _identifier(value: str, kind: str):
    if not value or not value.replace("_", "").isalnum():
        raise ValueError(
            f"{kind} must contain only letters, numbers, and underscores"
        )


class BuildContext(modict[str, object]):
    """Fixed inputs, plugin options and a read-only view of running services.

    One context belongs to one build. Contributions are collected while plugin
    declarations resolve, then sealed before any service factory runs.
    """

    _config = modict.config(frozen=True, auto_convert=False)

    def __init__(self, *args, **kwargs):
        inputs = dict(*args, **kwargs)
        if inputs.get("data_root") is not None:
            root = Path(inputs["data_root"]).expanduser().resolve()
            inputs["data_root"] = root
            for name, relative in (("session_path", "session.json"),
                                   ("surface_root", "surfaces")):
                inputs.setdefault(name, root / relative)
        options = inputs.pop("plugins", {})
        if not isinstance(options, Mapping) or any(
            not isinstance(name, str) or not name or not isinstance(value, Mapping)
            for name, value in options.items()
        ):
            raise TypeError("plugin options must map names to option mappings")
        super().__init__(inputs)
        object.__setattr__(self, "_plugin_options", MappingProxyType({
            name: MappingProxyType(dict(value)) for name, value in options.items()
        }))
        object.__setattr__(self, "_contributions", {})
        object.__setattr__(self, "_capabilities", MappingProxyType({}))
        object.__setattr__(self, "_services", ServiceContext())
        object.__setattr__(self, "_phase", "inputs")

    def plugin(self, name):
        """Fixed process-local options owned by one plugin."""
        return self._plugin_options.get(name, MappingProxyType({}))

    @property
    def application(self):
        """Application owning this construction, before service startup."""
        if not hasattr(self, "_application"):
            raise RuntimeError("build context is not bound to an application")
        return self._application

    @property
    def services(self):
        return self._services.view

    def _begin(self, capabilities, application):
        if self._phase != "inputs":
            raise RuntimeError("a BuildContext belongs to one application build")
        object.__setattr__(self, "_phase", "declarations")
        object.__setattr__(self, "_capabilities", capabilities)
        object.__setattr__(self, "_application", application)

    def _seal(self):
        object.__setattr__(self, "_phase", "services")

    def has_capability(self, name, min_version=1):
        """Whether the compiled application installs a compatible capability."""
        capability = self._capabilities.get(name)
        return capability is not None and capability.version >= min_version

    def contribute(self, name, value):
        """Collect a feature contribution independent of plugin resolution order."""
        if self._phase == "services":
            raise RuntimeError("contributions are sealed before service construction")
        if not isinstance(name, str) or not name:
            raise ValueError("contribution name must be a nonempty string")
        self._contributions.setdefault(name, []).append(value)
        return value

    def contributions(self, name):
        return tuple(self._contributions.get(name, ()))

    def require(self, name):
        try:
            return self[name]
        except KeyError as error:
            raise KeyError(f"missing build context value: {name}") from error


class Capability(modict):
    """A versioned public contract exported by a plugin runtime."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    name: str
    version: int = 1

    @modict.model_validator(mode="after")
    def validate_contract(self):
        if not self.name or "." not in self.name:
            raise ValueError("capability name must be namespaced")
        if self.version < 1:
            raise ValueError("capability version must be positive")


class CapabilityRequirement(modict):
    """Minimum compatible version of a public plugin capability."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    name: str
    min_version: int = 1

    @modict.model_validator(mode="after")
    def validate_contract(self):
        if not self.name or "." not in self.name:
            raise ValueError("capability requirement must be namespaced")
        if self.min_version < 1:
            raise ValueError("minimum capability version must be positive")


class ServerExtension(modict):
    """A service, its construction dependencies, lifecycle and client surfaces.

    Factories may request BuildContext as ``context`` alongside named services.
    Default surfaces discover decorated routes/sockets; empty tuples hide them.
    String factory references defer implementation imports until server build.
    """

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    service: object | None = None
    service_factory: Callable[..., object] | str | None = None
    endpoints: tuple[object, ...] | None = None
    websockets: tuple[object, ...] | None = None
    requires_services: tuple[str, ...] = ()
    optional_services: tuple[str, ...] = ()
    critical: bool = True
    scope: Literal["application", "session"] = "application"
    start: Callable | None = None
    stop: Callable | None = None
    health: Callable | None = None
    middleware: tuple[tuple[type, dict[str, Any]], ...] = ()
    mounts: tuple[tuple[str, object, str | None], ...] = ()

    def __init__(self, *args, **kwargs):
        declaration = dict(*args, **kwargs)
        factory = declaration.get("service_factory")
        if isinstance(factory, str):
            module, separator, attribute = factory.partition(":")
            if (
                not separator
                or not all(part.isidentifier() for part in module.split("."))
                or not attribute.isidentifier()
            ):
                raise ValueError("service factory reference must use module:factory")
        elif factory is not None:
            required = list(_tuple(declaration.get("requires_services")))
            optional = list(_tuple(declaration.get("optional_services")))
            for parameter in inspect.signature(factory).parameters.values():
                if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.VAR_POSITIONAL}:
                    raise ValueError("extension factory requires keyword parameters")
                if parameter.kind == parameter.VAR_KEYWORD or parameter.name == "context":
                    continue
                if parameter.name in optional and parameter.default is parameter.empty:
                    raise ValueError(f"optional service parameter requires a default: {parameter.name}")
                if parameter.name not in (*required, *optional):
                    target = required if parameter.default is parameter.empty else optional
                    target.append(parameter.name)
            declaration.update(requires_services=tuple(required), optional_services=tuple(optional))
        super().__init__(declaration)

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        _identifier(self.name, "extension name")
        if self.name == "context":
            raise ValueError("context is reserved for the build context, not a service")
        dependencies = (*self.requires_services, *self.optional_services)
        if "context" in dependencies:
            raise ValueError("context is an injected build input, not a service dependency")
        if self.name in dependencies:
            raise ValueError(f"extension cannot depend on itself: {self.name}")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"duplicate extension dependency: {self.name}")
        if self.service is not None and self.service_factory is not None:
            raise ValueError("extension service and factory are mutually exclusive")

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key in {"endpoints", "websockets"}:
            if isinstance(value, str):
                raise TypeError(f"extension {key} must contain declarations, not a discovery flag")
            if value is None:
                return None
        return _tuple(value) if key in {"endpoints", "websockets", "requires_services", "optional_services", "middleware", "mounts"} else value


class Plugin(modict):
    """One feature's agent binding, server service(s), and private agents."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    agent_extension: Callable | type | object | None = None
    server_extensions: tuple[ServerExtension, ...] = ()
    configure: Callable[[BuildContext], ServerExtension | tuple[ServerExtension, ...] | None] | str | None = None
    specialists: tuple[AgentTrigger, ...] = ()
    provides_capabilities: tuple[Capability, ...] = ()
    requires_capabilities: tuple[CapabilityRequirement, ...] = ()
    optional_capabilities: tuple[CapabilityRequirement, ...] = ()
    agent_binding_enabled: bool = True
    agent_binding_required: bool = False

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        _identifier(self.name, "plugin name")
        if self.agent_binding_required and not self.agent_binding_enabled:
            raise ValueError("a required plugin binding cannot start disabled")
        if self.agent_binding_required and self.agent_extension is None:
            raise ValueError("a server-only plugin cannot require an agent binding")
        if isinstance(self.configure, str):
            module, separator, attribute = self.configure.partition(":")
            if (
                not separator
                or not all(part.isidentifier() for part in module.split("."))
                or not attribute.isidentifier()
            ):
                raise ValueError(
                    f"plugin {self.name} configure reference must use module:factory"
                )
        if not all(isinstance(item, ServerExtension) for item in self.server_extensions):
            raise TypeError("plugin server_extensions must contain ServerExtension declarations")
        if self.configure is not None and not isinstance(self.configure, str) and not callable(self.configure):
            raise TypeError("plugin configure must be callable or an import reference")
        names = [agent.name for agent in self.specialists]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate private agent in plugin {self.name}")
        requirements = [item.name for item in (*self.requires_capabilities, *self.optional_capabilities)]
        if len(requirements) != len(set(requirements)):
            raise ValueError(f"duplicate plugin capability requirement: {self.name}")

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "specialists":
            invocations = tuple(
                AgentTrigger(spec=item)
                if isinstance(item, AgentSpec) else item for item in _tuple(value)
            )
            if any(not isinstance(item, AgentTrigger) for item in invocations):
                raise TypeError("specialists must contain AgentSpec or AgentTrigger declarations")
            if isinstance(self.get("agent_extension"), type):
                explicit = {item.name for item in invocations}
                discovered = (
                    AgentTrigger.from_function(member)
                    for _, member in inspect.getmembers(self.agent_extension)
                    if getattr(member, "agent_trigger", None) is not None
                )
                invocations += tuple(item for item in discovered if item.name not in explicit)
            return invocations
        return _tuple(value) if key in {"server_extensions", "specialists", "provides_capabilities", "requires_capabilities", "optional_capabilities"} else value

    def build_server_extensions(self, context: BuildContext):
        """Collect explicit declarations before any service is constructed."""
        declarations = self.server_extensions
        configure = self.configure
        if isinstance(configure, str):
            module, attribute = configure.split(":", 1)
            configure = getattr(import_module(module), attribute)
        if configure is not None:
            declarations = (*declarations, *_tuple(configure(context)))
        if not all(isinstance(item, ServerExtension) for item in declarations):
            raise TypeError(f"plugin {self.name} must declare ServerExtension components")
        return declarations
