"""Declarative composition for complete agent applications."""

from __future__ import annotations

import inspect
from importlib import import_module
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import Any, Literal
from pathlib import Path

from fastapi import FastAPI
from modict import modict

from ..api.declarations import Endpoint
from ...agent.extensions.extension import AgentExtension
from ...agent.spec import AgentSpec
from ...agent.extensions.specialists import AgentTrigger

from ..api.endpoints import EndpointRegistry, SecurityPolicy, endpoint_declarations
from ..api.plugins import PluginHostApi
from .plugins import PluginHost
from .controls import _bind_application_controls
from ..runtime.application import ApplicationRuntime
from .dependencies import dependency_order
from .services import ServiceContext, ServiceGraph
from ..api.websockets import WebSocketRegistry
from ..clients.surfaces import ClientSurface, SurfaceApplication, SurfaceService
from ..api.surfaces import SurfaceApi


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


def _paths_overlap(left, right):
    left = left.rstrip("/") or "/"
    right = right.rstrip("/") or "/"
    return (
        left == "/"
        or right == "/"
        or left == right
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
    )


def _path_within_mount(path, mount):
    mount = mount.rstrip("/") or "/"
    return mount == "/" or path == mount or path.startswith(f"{mount}/")


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
                                   ("plugin_state_path", "plugins.json"),
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
    def services(self):
        return self._services.view

    def _begin(self, capabilities):
        if self._phase != "inputs":
            raise RuntimeError("a BuildContext belongs to one application build")
        object.__setattr__(self, "_phase", "declarations")
        object.__setattr__(self, "_capabilities", capabilities)

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
    """One coherent server capability and its declarative surface."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    service: object | None = None
    service_factory: Callable[..., object] | None = None
    endpoints: tuple[object, ...] | Literal["service"] = ()
    websockets: tuple[object, ...] | Literal["service"] = ()
    requires_services: tuple[str, ...] = ()
    optional_services: tuple[str, ...] = ()
    critical: bool = True
    start: Callable | None = None
    stop: Callable | None = None
    health: Callable | None = None
    middleware: tuple[tuple[type, dict[str, Any]], ...] = ()
    mounts: tuple[tuple[str, object, str | None], ...] = ()

    def __init__(self, *args, **kwargs):
        declaration = dict(*args, **kwargs)
        factory = declaration.get("service_factory")
        if factory is not None:
            required = list(_tuple(declaration.get("requires_services")))
            optional = list(_tuple(declaration.get("optional_services")))
            for parameter in inspect.signature(factory).parameters.values():
                if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.VAR_POSITIONAL}:
                    raise ValueError("extension factory requires keyword parameters")
                if parameter.kind == parameter.VAR_KEYWORD:
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
        dependencies = (*self.requires_services, *self.optional_services)
        if self.name in dependencies:
            raise ValueError(f"extension cannot depend on itself: {self.name}")
        if len(dependencies) != len(set(dependencies)):
            raise ValueError(f"duplicate extension dependency: {self.name}")
        if self.service is not None and self.service_factory is not None:
            raise ValueError("extension service and factory are mutually exclusive")
        if isinstance(self.endpoints, str) and self.endpoints != "service":
            raise ValueError("extension endpoints must use 'service' or declarations")
        if isinstance(self.websockets, str) and self.websockets != "service":
            raise ValueError("extension websockets must use 'service' or declarations")

    def with_endpoints(self, *endpoints):
        if self.endpoints == "service":
            raise ValueError("cannot append endpoints to automatic service discovery")
        return type(self)({**self, "endpoints": (*self.endpoints, *endpoints)})

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key in {"endpoints", "websockets"} and value == "service":
            return value
        return _tuple(value) if key in {"endpoints", "websockets", "requires_services", "optional_services", "middleware", "mounts"} else value


class Plugin(modict):
    """One feature's agent binding, server service(s), and private agents."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    agent_extension: Callable | type | object | None = None
    server_extension: object | None = None
    specialists: tuple[AgentSpec, ...] = ()
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
        if isinstance(self.server_extension, str):
            module, separator, attribute = self.server_extension.partition(":")
            if (
                not separator
                or not all(part.isidentifier() for part in module.split("."))
                or not attribute.isidentifier()
            ):
                raise ValueError(
                    f"plugin {self.name} server extension reference must use module:factory"
                )
        names = [agent.name for agent in self.specialists]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate private agent in plugin {self.name}")
        requirements = [item.name for item in (*self.requires_capabilities, *self.optional_capabilities)]
        if len(requirements) != len(set(requirements)):
            raise ValueError(f"duplicate plugin capability requirement: {self.name}")

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "specialists" and not value and isinstance(self.get("agent_extension"), type):
            return tuple(
                AgentTrigger.from_function(member).spec
                for _, member in inspect.getmembers(self.agent_extension)
                if getattr(member, "agent_trigger", None) is not None
            )
        return _tuple(value) if key in {"specialists", "provides_capabilities", "requires_capabilities", "optional_capabilities"} else value

    def build_server_extensions(self, context: BuildContext):
        if self.server_extension is None:
            return None
        runtime = self.server_extension
        if isinstance(runtime, str):
            module, attribute = runtime.split(":", 1)
            runtime = getattr(import_module(module), attribute)
        if not isinstance(runtime, (ServerExtension, tuple)) and callable(runtime):
            runtime = runtime(context)
        if not isinstance(runtime, (ServerExtension, tuple)):
            has_endpoints = _service_declares_endpoints(runtime)
            if not (
                has_endpoints
                or callable(getattr(runtime, "websocket_declarations", None))
                or callable(getattr(runtime, "start", None))
                or callable(getattr(runtime, "stop", None))
                or callable(getattr(runtime, "health", None))
            ):
                raise TypeError(
                    f"plugin {self.name} runtime must be a server service "
                    "or ServerExtension declaration"
                )
            runtime = ServerExtension(
                name=self.name,
                service=runtime,
                endpoints="service" if has_endpoints else (),
                websockets=(
                    "service" if callable(getattr(runtime, "websocket_declarations", None))
                    else ()
                ),
            )
        extensions = _tuple(runtime)
        if not all(isinstance(item, ServerExtension) for item in extensions):
            raise TypeError(f"plugin {self.name} did not produce ServerExtension declarations")
        return extensions

class ApplicationPlan(modict):
    """Validated immutable output of application compilation."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    application: ApplicationSpec
    primary_agent: AgentSpec
    agents: Mapping[str, AgentSpec]
    plugins: Mapping[str, Plugin]
    capabilities: Mapping[str, Capability]


class ApplicationSpec(modict):
    """Complete declaration of an agent, its runtime and client surfaces."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    version: str
    primary_agent: AgentSpec
    plugins: tuple[Plugin, ...] = ()
    server_extensions: tuple[ServerExtension, ...] = ()
    surfaces: tuple[ClientSurface, ...] = ()
    security: SecurityPolicy | None = None
    title: str | None = None
    description: str | None = None
    docs_url: str | None = "/docs"
    validate_responses: bool = True

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if not self.name.strip():
            raise ValueError("application name is required")
        if not self.version.strip():
            raise ValueError("application version is required")
        if self.primary_agent.session_mode != "durable":
            raise ValueError("the primary agent session must be durable")

    def with_server_extensions(self, *extensions):
        return type(self)({**self, "server_extensions": (*self.server_extensions, *extensions)})

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "plugins":
            plugins = []
            for declaration in _tuple(value):
                if isinstance(declaration, type) and issubclass(declaration, AgentExtension):
                    if not declaration.name:
                        raise ValueError("a plugin class declaration requires an explicit name")
                    declaration = Plugin(name=declaration.name, agent_extension=declaration)
                plugins.append(declaration)
            return tuple(plugins)
        return _tuple(value) if key in {"plugins", "server_extensions", "surfaces"} else value

    def compile(self):
        return _compile_application(self)

    def build(self, context: BuildContext | None = None):
        return build_application(self, context=context)

    def build_conversation(
        self, reference: str, context: BuildContext, *,
        security: SecurityPolicy | None = None,
        handshake: Callable | None = None,
        event_handshake: Callable | None = None,
        application_handshake: Callable | None = None,
        supervisor: object | None = None,
        runtime_factory: Callable | None = None,
        session_projection: str = "full",
        health_projection: Callable | None = None,
        reset_handler: Callable | None = None,
        extensions: tuple[ServerExtension, ...] | Callable = (),
    ):
        """Assemble one durable worker, its API, health and authenticated sockets.

        ``reference`` is the importable ``module:application`` used by workers.
        ``handshake`` authenticates both sockets unless individually overridden.
        Advanced applications can supply a supervisor or a runtime factory,
        project health/history, and declare extra extensions from the runtime.
        ``reset_handler(runtime)`` owns the application's selective reset policy.
        """
        from ..runtime.supervisor import WorkerSupervisor
        from ..api.agent import CanonicalAgentApi
        from ..api.health import ApplicationHealthApi
        from .runtime import _conversation_extensions

        module, separator, attribute = reference.partition(":")
        if not separator or not attribute.isidentifier() or not all(
            part.isidentifier() for part in module.split(".")
        ):
            raise ValueError("conversation reference must use module:application")
        security = security if security is not None else self.security
        if security is None:
            raise ValueError("a conversation requires an explicit security policy")
        event_handshake = event_handshake or handshake
        application_handshake = application_handshake or handshake
        if not callable(event_handshake) or not callable(application_handshake):
            raise ValueError("a conversation requires authenticated socket handshakes")
        context.require("data_root")
        if supervisor is None:
            supervisor = WorkerSupervisor(
                context.require("session_path"), application_reference=reference,
                agent_name=self.primary_agent.name,
                plugin_state_path=context.require("plugin_state_path"),
            )
        else:
            path = getattr(supervisor, "session_path", None)
            if path is not None and Path(path).expanduser().resolve() != Path(context.require("session_path")).expanduser().resolve():
                raise ValueError("supervisor and build context must use the same session")
            worker_reference = getattr(supervisor, "application_reference", None)
            if worker_reference is not None and worker_reference != reference:
                raise ValueError("supervisor and conversation must use the same application reference")
        runtime = (
            runtime_factory(supervisor) if runtime_factory is not None
            else ApplicationRuntime(supervisor, restart=context.get("restart"))
        )
        extra = extensions(runtime) if callable(extensions) else extensions
        definition = type(self)({
            **self, "security": security,
            "server_extensions": (
                *self.server_extensions,
                *_conversation_extensions(
                    runtime, event_handshake=event_handshake,
                    application_handshake=application_handshake,
                    agent_api=CanonicalAgentApi(
                        runtime, session_projection=session_projection,
                        reset_handler=(lambda: reset_handler(runtime)) if reset_handler else None,
                    ),
                    health_api=ApplicationHealthApi(health_projection, runtime=runtime),
                ),
                *_tuple(extra),
            ),
        })
        return definition.build(context)


class ApplicationContext:
    """Single process-local access point for a compiled application."""

    def __init__(self, plan, server_extensions, *, services):
        self.spec = plan.application
        self.plan = plan
        self.services = services
        self.server_extensions = MappingProxyType(server_extensions)
        self.plugins = plan.plugins
        self.service_graph = None
        self.plugin_host = None
        self.surface_service = None

    def service(self, name):
        return self.services.require(name)


def _compile_application(application):
    plugins = {}
    capabilities = {}
    extensions = list(application.server_extensions)
    extension_map = {}
    for extension in extensions:
        if not isinstance(extension, ServerExtension):
            raise TypeError("application extensions must be ServerExtension instances")
        if extension.name in extension_map:
            raise ValueError(f"duplicate application extension: {extension.name}")
        extension_map[extension.name] = extension
    runtime_names = set(extension_map)
    agent_contributions = []
    private_identities = set()
    agents = {}
    specialist_profiles = []
    for plugin in application.plugins:
        if not isinstance(plugin, Plugin):
            raise TypeError("application plugins must be Plugin instances")
        if plugin.name in plugins:
            raise ValueError(f"duplicate plugin: {plugin.name}")
        plugins[plugin.name] = plugin
        triggers = {
            trigger.spec.name: trigger
            for _, member in inspect.getmembers(plugin.agent_extension)
            if getattr(member, "agent_trigger", None) is not None
            for trigger in (AgentTrigger.from_function(member),)
        }
        for private_agent in plugin.specialists:
            if not isinstance(private_agent, AgentSpec):
                raise TypeError(
                    f"private agents for plugin {plugin.name} must be AgentSpec instances"
                )
            identity = f"{plugin.name}.{private_agent.name}"
            if identity == application.primary_agent.name or identity in private_identities:
                raise ValueError(f"duplicate private agent identity: {identity}")
            private_identities.add(identity)
            compiled_agent = AgentSpec({**private_agent, "name": identity})
            agents[identity] = compiled_agent
            trigger = triggers.get(private_agent.name)
            specialist_profiles.append(
                compiled_agent.fleet_profile(
                    identity=identity,
                    **({key: trigger[key] for key in (
                        "observes", "item_kinds", "conversation_roles", "input_item_field"
                    )} if trigger is not None else {}),
                )
            )
        for capability in plugin.provides_capabilities:
            if capability.name in capabilities:
                raise ValueError(f"duplicate plugin capability: {capability.name}")
            capabilities[capability.name] = capability
        # A worker must be able to compile the agent projection without
        # constructing server-only resources or needing server credentials.
        static_runtime = plugin.server_extension if isinstance(plugin.server_extension, (ServerExtension, tuple)) else ()
        static_extensions = _tuple(static_runtime)
        if not all(isinstance(item, ServerExtension) for item in static_extensions):
            raise TypeError(f"plugin {plugin.name} server contribution must contain ServerExtension declarations")
        for extension in static_extensions:
            if extension.name in runtime_names:
                raise ValueError(
                    f"duplicate plugin runtime extension: {extension.name}"
                )
            runtime_names.add(extension.name)
        if plugin.agent_extension is not None:
            agent_contributions.append(plugin)
    for plugin in plugins.values():
        for requirement in plugin.requires_capabilities:
            capability = capabilities.get(requirement.name)
            if capability is None or capability.version < requirement.min_version:
                raise ValueError(
                    f"plugin {plugin.name} requires unavailable capability "
                    f"{requirement.name}>={requirement.min_version}"
                )
        for requirement in plugin.optional_capabilities:
            capability = capabilities.get(requirement.name)
            if capability is not None and capability.version < requirement.min_version:
                raise ValueError(
                    f"plugin {plugin.name} cannot integrate capability "
                    f"{requirement.name}>={requirement.min_version}"
                )
    _plugin_dependency_order(plugins)

    dependency_order(
        {
            name: (
                *(dependency for dependency in extension.requires_services
                  if dependency in extension_map),
                *(dependency for dependency in extension.optional_services
                  if dependency in extension_map),
            )
            for name, extension in extension_map.items()
        },
        kind="application extension",
    )
    surface_routes = [
        route for surface in application.surfaces
        for route in (*surface.routes, surface.preview_route)
    ]
    if any(
        _paths_overlap(left, right)
        for index, left in enumerate(surface_routes)
        for right in surface_routes[index + 1:]
    ):
        raise ValueError("overlapping client surface route")
    mount_paths = [
        path for extension in extensions for path, _app, _name in extension.mounts
    ]
    collisions = {
        f"{mount} <> {surface}"
        for mount in mount_paths
        for surface in surface_routes
        if _paths_overlap(mount, surface)
    }
    if collisions:
        raise ValueError(
            "client surface route collides with application mount: "
            + ", ".join(sorted(collisions))
        )
    primary_agent = application.primary_agent.with_extensions(
        *agent_contributions
    ).with_specialist_profiles(*specialist_profiles)
    agents[primary_agent.name] = primary_agent
    return ApplicationPlan(
        application=application,
        primary_agent=primary_agent,
        agents=MappingProxyType(agents),
        plugins=MappingProxyType(plugins),
        capabilities=MappingProxyType(capabilities),
    )


def _service_declares_endpoints(service):
    """Recognize endpoint-bearing services without invoking their handlers."""
    if isinstance(service, Endpoint):
        return True
    if callable(service) and getattr(service, "http_endpoint", None) is not None:
        return True
    if callable(getattr(service, "endpoint_declarations", None)):
        return True
    return any(
        getattr(member, "http_endpoint", None) is not None
        for _, member in inspect.getmembers(type(service))
    )


def _resolve_plugin_server_extensions(plan, context):
    names = {extension.name for extension in plan.application.server_extensions}
    extensions = {}
    for plugin_name in _plugin_dependency_order(plan.plugins):
        plugin = plan.plugins[plugin_name]
        plugin_runtime = plugin.build_server_extensions(context)
        if plugin_runtime is None:
            continue
        for extension in plugin_runtime:
            if extension.name in names:
                raise ValueError(f"duplicate plugin runtime extension: {extension.name}")
            names.add(extension.name)
        extensions[plugin.name] = plugin_runtime
    return extensions


def _extension_dependencies(plan, plugin_server_extensions):
    """One startup DAG for application and plugin-owned server components."""
    all_extensions = (
        *plan.application.server_extensions,
        *(extension for group in plugin_server_extensions.values() for extension in group),
    )
    names = {extension.name for extension in all_extensions}
    extension_owners = {
        extension.name: plugin_name
        for plugin_name, group in plugin_server_extensions.items()
        for extension in group
    }

    def direct_dependencies(extension):
        return (
            *extension.requires_services,
            *(name for name in extension.optional_services if name in names),
        )

    exports = {
        capability.name: name
        for name, plugin in plan.plugins.items()
        for capability in plugin.provides_capabilities
    }
    dependencies = {
        extension.name: direct_dependencies(extension)
        for extension in plan.application.server_extensions
    }
    for name, group in plugin_server_extensions.items():
        providers = tuple(dict.fromkeys(
            exports[requirement.name]
            for requirement in (
                *plan.plugins[name].requires_capabilities,
                *plan.plugins[name].optional_capabilities,
            )
            if requirement.name in exports
        ))
        provider_extensions = tuple(
            extension.name
            for provider in providers
            for extension in plugin_server_extensions.get(provider, ())
        )
        for extension in group:
            for dependency in direct_dependencies(extension):
                owner = extension_owners.get(dependency)
                if owner is not None and owner != name and owner not in providers:
                    raise ValueError(
                        f"plugin {name} uses service {dependency} from plugin {owner} "
                        "without a versioned capability requirement"
                    )
            dependencies[extension.name] = tuple(dict.fromkeys((
                *direct_dependencies(extension), *provider_extensions,
            )))
    return dependencies


def _plugin_dependency_order(plugins):
    exports = {
        capability.name: name
        for name, plugin in plugins.items()
        for capability in plugin.provides_capabilities
    }
    return dependency_order({
        name: tuple(
            exports[requirement.name]
            for requirement in (*plugin.requires_capabilities, *plugin.optional_capabilities)
            if requirement.name in exports
        )
        for name, plugin in plugins.items()
    }, kind="plugin")


def _construct_extension_service(extension, services):
    service = extension.service
    if extension.service_factory is not None:
        dependencies = {
            dependency: services[dependency]
            for dependency in (*extension.requires_services, *extension.optional_services)
            if dependency in services
        }
        parameters = inspect.signature(extension.service_factory).parameters
        if not any(item.kind == item.VAR_KEYWORD for item in parameters.values()):
            dependencies = {name: value for name, value in dependencies.items() if name in parameters}
        service = extension.service_factory(**dependencies)
        if inspect.isawaitable(service):
            raise TypeError(
                f"extension factory must be synchronous: {extension.name}"
            )
    endpoints = extension.endpoints
    if endpoints == "service":
        if service is None or not _service_declares_endpoints(service):
            raise ValueError(
                f"extension {extension.name} requested service endpoints "
                "without a decorated service"
            )
        endpoints = (service,)
    websockets = extension.websockets
    if websockets == "service":
        declarations = getattr(service, "websocket_declarations", None)
        if not callable(declarations):
            raise ValueError(
                f"extension {extension.name} requested service WebSockets "
                "without websocket_declarations()"
            )
        websockets = tuple(declarations())
    if (
        service is extension.service
        and endpoints is extension.endpoints
        and websockets is extension.websockets
    ):
        return extension
    return ServerExtension({
        **extension,
        "service": service,
        "service_factory": None,
        "endpoints": endpoints,
        "websockets": websockets,
    })


def build_application(
    application: ApplicationSpec,
    *,
    context: BuildContext | None = None,
) -> FastAPI:
    """Compile the complete declaration, then build its FastAPI runtime."""
    if not isinstance(application, ApplicationSpec):
        raise TypeError("build_application expects an ApplicationSpec")
    plan = application.compile()
    build_context = context if context is not None else BuildContext()
    build_context._begin(plan.capabilities)
    plugin_server_extensions = _resolve_plugin_server_extensions(plan, build_context)
    build_context._seal()
    declarations = {
        extension.name: extension
        for extension in (
            *plan.application.server_extensions,
            *(item for group in plugin_server_extensions.values() for item in group),
        )
    }
    extension_dependencies = _extension_dependencies(plan, plugin_server_extensions)
    startup_order = dependency_order(extension_dependencies, kind="application extension")
    resolved_services = {}
    resolved_extensions = {}
    for name in startup_order:
        extension = _construct_extension_service(declarations[name], resolved_services)
        resolved_extensions[name] = extension
        if extension.service is not None:
            resolved_services[name] = extension.service
    runtime = ApplicationContext(plan, resolved_extensions, services=build_context._services)
    agent_runtimes = [service for service in resolved_services.values() if isinstance(service, ApplicationRuntime)]
    if len(agent_runtimes) > 1:
        raise ValueError("an application may install only one primary agent runtime")
    agent_runtime = agent_runtimes[0] if agent_runtimes else None

    services = []
    for name in startup_order:
        extension = resolved_extensions[name]
        if extension.service is None:
            if any((extension.start, extension.stop, extension.health)):
                raise ValueError(
                    f"extension {extension.name} declares lifecycle hooks without a service"
                )
            continue
        services.append(extension)
    service_names = {extension.name for extension in services}
    graph = ServiceGraph(
        services, context=runtime.services,
        dependencies={
            extension.name: tuple(
                dependency for dependency in extension_dependencies[extension.name]
                if dependency in service_names
            )
            for extension in services
        },
    )
    graph.resolve_order()
    runtime.service_graph = graph

    app = FastAPI(
        title=application.title or application.name,
        description=application.description,
        version=application.version,
        docs_url=application.docs_url,
    )
    app.state.application = runtime
    plugin_owners = {
        extension.name: plugin_name
        for plugin_name, group in plugin_server_extensions.items()
        for extension in group
    }
    all_extensions = tuple(
        (runtime.server_extensions[name], plugin_owners.get(name, name))
        for name in startup_order
    )
    mounts = []
    middleware_entries = []
    for extension, _owner in all_extensions:
        middleware_entries.extend(
            (middleware, dict(options))
            for middleware, options in extension.middleware
        )
        for path, mounted_app, name in extension.mounts:
            if not path.startswith("/"):
                raise ValueError(
                    f"extension mount path must start with /: {extension.name}"
                )
            if any(_paths_overlap(path, existing) for existing, _, _ in mounts):
                raise ValueError(f"overlapping application mount: {path}")
            if any(
                _paths_overlap(path, route)
                for surface in application.surfaces
                for route in (*surface.routes, surface.preview_route)
            ):
                raise ValueError(
                    f"client surface route collides with application mount: {path}"
                )
            mounts.append((path, mounted_app, name))
    mount_prefixes = tuple(path for path, _, _ in mounts)
    surface_prefixes = tuple(
        route
        for surface in application.surfaces
        for route in (*surface.routes, surface.preview_route)
    )
    allowed_surface_routes = {
        route
        for surface in application.surfaces
        for route in surface.server_routes
    }
    registry = EndpointRegistry(
        app,
        security=application.security,
        validate_responses=application.validate_responses,
        application_context=runtime,
        reserved_prefixes=mount_prefixes,
    )
    socket_registry = WebSocketRegistry(app, security=application.security)
    for extension, owner in all_extensions:
        for source in extension.endpoints:
            for declaration in endpoint_declarations(source):
                if declaration.path not in allowed_surface_routes and any(
                    _path_within_mount(declaration.path, prefix)
                    for prefix in surface_prefixes
                ):
                    raise ValueError(
                        f"application endpoint is shadowed by a mount: {declaration.path}"
                    )
                registry.add(declaration, owner=owner)
        for endpoint in extension.websockets:
            if any(_path_within_mount(endpoint.path, prefix) for prefix in mount_prefixes):
                raise ValueError(
                    f"application WebSocket is shadowed by a mount: {endpoint.path}"
                )
            if endpoint.path not in allowed_surface_routes and any(
                _path_within_mount(endpoint.path, prefix)
                for prefix in surface_prefixes
            ):
                raise ValueError(
                    f"application WebSocket is shadowed by a mount: {endpoint.path}"
                )
            socket_registry.add(endpoint, owner=owner)
    if plan.plugins and application.security is not None:
        for endpoint in endpoint_declarations(PluginHostApi(runtime)):
            registry.add(endpoint, owner="plugin_host")
    if application.surfaces:
        if application.security is None:
            raise ValueError("client surfaces require a security policy")
        for endpoint in endpoint_declarations(SurfaceApi(runtime)):
            registry.add(endpoint, owner="surface_service")
    registry.install()
    socket_registry.install()
    for middleware, options in middleware_entries:
        app.add_middleware(middleware, **options)
    for path, mounted_app, name in mounts:
        app.mount(path, mounted_app, name=name)
    if application.surfaces:
        surface_root = build_context.get("surface_root")
        if surface_root is None:
            raise ValueError("client surfaces require surface_root in BuildContext")
        surface_service = SurfaceService(
            application.surfaces,
            surface_root,
            notify=build_context.get("surface_notify", agent_runtime.surface_notify if agent_runtime else None),
            progress=build_context.get("surface_progress", agent_runtime.surface_progress if agent_runtime else None),
        )
        runtime.surface_service = surface_service
        for surface in application.surfaces:
            for route in surface.routes:
                app.mount(
                    route,
                    SurfaceApplication(surface_service, surface.name),
                    name=f"surface:{surface.name}:{route}",
                )
            app.mount(
                surface.preview_route,
                SurfaceApplication(
                    surface_service,
                    surface.name,
                    preview=True,
                    security=application.security,
                ),
                name=f"surface-preview:{surface.name}",
            )
    binding_updates = build_context.contributions("plugins.binding_update")
    if len(binding_updates) > 1:
        raise ValueError("only one plugin binding coordinator may be installed")
    plugin_host = PluginHost(
        plan, service_graph=graph, service_owners=MappingProxyType(plugin_owners),
        state_path=build_context.get("plugin_state_path"),
        binding_update=build_context.get("plugin_binding_update", binding_updates[0] if binding_updates else agent_runtime.set_plugin_binding if agent_runtime else None),
        binding_snapshot=build_context.get("plugin_binding_snapshot", agent_runtime.plugin_bindings if agent_runtime else None),
    )
    runtime.plugin_host = plugin_host
    if agent_runtime is not None:
        _bind_application_controls(agent_runtime, app, ui_root=build_context.get("ui_root"))

    @asynccontextmanager
    async def lifespan(_app):
        await graph.start()
        try:
            await plugin_host.start()
            yield
        finally:
            await graph.stop()

    app.router.lifespan_context = lifespan
    return app


__all__ = [
    "ApplicationSpec",
    "ApplicationPlan",
    "BuildContext",
    "Capability",
    "CapabilityRequirement",
    "ClientSurface",
    "ServerExtension",
    "Plugin",
    "build_application",
]
