"""Declarative composition for complete agent applications."""

from __future__ import annotations

import inspect
from importlib import import_module
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from typing import Any, Literal

from fastapi import FastAPI
from modict import modict

from ...agent.extensions.endpoints import Endpoint
from ...agent.extensions.plugin import AgentPlugin
from ...agent.spec import AgentSpec
from ...agent.extensions.specialists import AgentTrigger

from ..api.endpoints import EndpointRegistry, SecurityPolicy
from ..api.plugins import PluginHostApi
from .plugins import PluginHost
from .dependencies import dependency_order
from .services import ServiceContext, ServiceGraph, ServiceSpec
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
    """Process-local inputs and ordered plugin construction-time exports."""

    _config = modict.config(frozen=True, auto_convert=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "_provided", {})
        object.__setattr__(self, "_contributions", {})
        object.__setattr__(self, "_capabilities", MappingProxyType({}))

    def has_capability(self, name, min_version=1):
        """Whether the compiled application installs a compatible capability."""
        capability = self._capabilities.get(name)
        return capability is not None and capability.version >= min_version

    def provide(self, name, value):
        """Expose a prepared value to plugins resolved later in the dependency DAG."""
        if not isinstance(name, str) or not name:
            raise ValueError("provided build context name must be a nonempty string")
        if name in self or name in self._provided:
            raise ValueError(f"duplicate build context value: {name}")
        self._provided[name] = value
        return value

    def contribute(self, name, value):
        """Collect a feature contribution independent of plugin resolution order."""
        if not isinstance(name, str) or not name:
            raise ValueError("contribution name must be a nonempty string")
        self._contributions.setdefault(name, []).append(value)
        return value

    def contributions(self, name):
        return tuple(self._contributions.get(name, ()))

    def get(self, name, default=None):
        if name in self._provided:
            return self._provided[name]
        return super().get(name, default)

    def require(self, name):
        if name in self._provided:
            return self._provided[name]
        if name in self:
            return self[name]
        raise KeyError(f"missing build context value: {name}")


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


class Extension(modict):
    """One coherent server capability and its declarative surface."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    service: object | None = None
    service_factory: Callable[..., object] | None = None
    endpoints: tuple[object, ...] | Literal["service"] = ()
    websockets: tuple[object, ...] | Literal["service"] = ()
    requires: tuple[str, ...] = ()
    optional_requires: tuple[str, ...] = ()
    critical: bool = True
    start: Callable | None = None
    stop: Callable | None = None
    health: Callable | None = None
    middleware: tuple[tuple[type, dict[str, Any]], ...] = ()
    mounts: tuple[tuple[str, object, str | None], ...] = ()

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        _identifier(self.name, "extension name")
        dependencies = (*self.requires, *self.optional_requires)
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
        return _tuple(value) if key in {"endpoints", "websockets", "requires", "optional_requires", "middleware", "mounts"} else value


class Plugin(modict):
    """One feature's agent binding, server service(s), and private agents."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    agent: Callable | type | object | None = None
    runtime: object | None = None
    agents: tuple[AgentSpec, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    requires: tuple[CapabilityRequirement, ...] = ()
    optional_requires: tuple[CapabilityRequirement, ...] = ()
    binding_enabled: bool = True
    binding_required: bool = False

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        _identifier(self.name, "plugin name")
        if self.binding_required and not self.binding_enabled:
            raise ValueError("a required plugin binding cannot start disabled")
        if self.binding_required and self.agent is None:
            raise ValueError("a server-only plugin cannot require an agent binding")
        if isinstance(self.runtime, str):
            module, separator, attribute = self.runtime.partition(":")
            if (
                not separator
                or not all(part.isidentifier() for part in module.split("."))
                or not attribute.isidentifier()
            ):
                raise ValueError(
                    f"plugin {self.name} runtime reference must use module:factory"
                )
        names = [agent.name for agent in self.agents]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate private agent in plugin {self.name}")
        requirements = [item.name for item in (*self.requires, *self.optional_requires)]
        if len(requirements) != len(set(requirements)):
            raise ValueError(f"duplicate plugin capability requirement: {self.name}")

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "agents" and not value and isinstance(self.get("agent"), type):
            return tuple(
                AgentTrigger.from_function(member).spec
                for _, member in inspect.getmembers(self.agent)
                if getattr(member, "agent_trigger", None) is not None
            )
        return _tuple(value) if key in {"agents", "capabilities", "requires", "optional_requires"} else value

    def runtime_extension(self, context: BuildContext):
        if self.runtime is None:
            return None
        runtime = self.runtime
        if isinstance(runtime, str):
            module, attribute = runtime.split(":", 1)
            runtime = getattr(import_module(module), attribute)
        if not isinstance(runtime, (Extension, tuple)) and callable(runtime):
            runtime = runtime(context)
        if not isinstance(runtime, (Extension, tuple)):
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
                    "or Extension declaration"
                )
            runtime = Extension(
                name=self.name,
                service=runtime,
                endpoints="service" if has_endpoints else (),
                websockets=(
                    "service" if callable(getattr(runtime, "websocket_declarations", None))
                    else ()
                ),
            )
        extensions = _tuple(runtime)
        if not all(isinstance(item, Extension) for item in extensions):
            raise TypeError(f"plugin {self.name} did not produce Extensions")
        return extensions

    def agent_plugin(self):
        return self.agent


class ApplicationPlan(modict):
    """Validated immutable output of application compilation."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    application: AgentApplication
    primary_agent: AgentSpec
    agents: Mapping[str, AgentSpec]
    plugins: Mapping[str, Plugin]
    plugin_extensions: Mapping[str, tuple[Extension, ...]]
    extensions: tuple[Extension, ...]
    capabilities: Mapping[str, Capability]
    extension_order: tuple[str, ...]


class AgentApplication(modict):
    """Complete declaration of an agent, its runtime and client surfaces."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    version: str
    primary_agent: AgentSpec
    plugins: tuple[Plugin, ...] = ()
    extensions: tuple[Extension, ...] = ()
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
        if self.primary_agent.session.mode != "durable":
            raise ValueError("the primary agent session must be durable")

    def with_extensions(self, *extensions):
        return type(self)({**self, "extensions": (*self.extensions, *extensions)})

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "plugins":
            plugins = []
            for declaration in _tuple(value):
                if isinstance(declaration, type) and issubclass(declaration, AgentPlugin):
                    if not declaration.name:
                        raise ValueError("a plugin class declaration requires an explicit name")
                    declaration = Plugin(name=declaration.name, agent=declaration)
                plugins.append(declaration)
            return tuple(plugins)
        return _tuple(value) if key in {"plugins", "extensions", "surfaces"} else value

    def compile(self):
        return _compile_application(self)

    def build(self, context: BuildContext | None = None):
        return build_application(self, context=context)


class ApplicationContext:
    """Single process-local access point for a compiled application."""

    def __init__(self, plan):
        self.spec = plan.application
        self.plan = plan
        self.services = ServiceContext()
        self.extensions = {
            item.name: item
            for item in (
                *plan.extensions,
                *(extension for group in plan.plugin_extensions.values() for extension in group),
            )
        }
        self.plugins = dict(plan.plugins)
        self.service_graph = None
        self.plugin_host = None
        self.surface_service = None

    def service(self, name):
        return self.services.require(name)


def _compile_application(application):
    plugins = {}
    capabilities = {}
    extensions = list(application.extensions)
    extension_map = {}
    for extension in extensions:
        if not isinstance(extension, Extension):
            raise TypeError("application extensions must be Extension instances")
        if extension.name in extension_map:
            raise ValueError(f"duplicate application extension: {extension.name}")
        extension_map[extension.name] = extension
    runtime_names = set(extension_map)
    plugin_extensions = {}
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
            for _, member in inspect.getmembers(plugin.agent)
            if getattr(member, "agent_trigger", None) is not None
            for trigger in (AgentTrigger.from_function(member),)
        }
        for private_agent in plugin.agents:
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
        for capability in plugin.capabilities:
            if capability.name in capabilities:
                raise ValueError(f"duplicate plugin capability: {capability.name}")
            capabilities[capability.name] = capability
        # A worker must be able to compile the agent projection without
        # constructing server-only resources or needing server credentials.
        static_runtime = plugin.runtime if isinstance(plugin.runtime, (Extension, tuple)) else ()
        static_extensions = _tuple(static_runtime)
        if not all(isinstance(item, Extension) for item in static_extensions):
            raise TypeError(f"plugin {plugin.name} runtime must contain Extensions")
        for extension in static_extensions:
            if extension.name in runtime_names:
                raise ValueError(
                    f"duplicate plugin runtime extension: {extension.name}"
                )
            runtime_names.add(extension.name)
        if static_extensions:
            plugin_extensions[plugin.name] = static_extensions
        if plugin.agent_plugin() is not None:
            agent_contributions.append(plugin)
    for plugin in plugins.values():
        for requirement in plugin.requires:
            capability = capabilities.get(requirement.name)
            if capability is None or capability.version < requirement.min_version:
                raise ValueError(
                    f"plugin {plugin.name} requires unavailable capability "
                    f"{requirement.name}>={requirement.min_version}"
                )
        for requirement in plugin.optional_requires:
            capability = capabilities.get(requirement.name)
            if capability is not None and capability.version < requirement.min_version:
                raise ValueError(
                    f"plugin {plugin.name} cannot integrate capability "
                    f"{requirement.name}>={requirement.min_version}"
                )
    _plugin_dependency_order(plugins)

    extension_order = dependency_order(
        {
            name: (
                *(dependency for dependency in extension.requires
                  if dependency in extension_map),
                *(dependency for dependency in extension.optional_requires
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
    primary_agent = application.primary_agent.with_plugins(
        *agent_contributions
    ).with_specialists(*specialist_profiles)
    agents[primary_agent.name] = primary_agent
    return ApplicationPlan(
        application=application,
        primary_agent=primary_agent,
        agents=MappingProxyType(agents),
        plugins=MappingProxyType(plugins),
        plugin_extensions=MappingProxyType(plugin_extensions),
        extensions=tuple(extensions),
        capabilities=MappingProxyType(capabilities),
        extension_order=tuple(extension_order),
    )


def _declared_endpoints(source):
    if isinstance(source, Endpoint):
        return (source,)
    if callable(source) and getattr(source, "agent_endpoint", None) is not None:
        return (Endpoint.from_function(source),)
    declarations = getattr(source, "endpoint_declarations", None)
    if callable(declarations):
        return tuple(declarations())
    endpoints = []
    for name, member in inspect.getmembers(type(source)):
        if getattr(member, "agent_endpoint", None) is None:
            continue
        endpoints.append(Endpoint.from_function(getattr(source, name)))
    if endpoints:
        return tuple(endpoints)
    raise TypeError("extension endpoint must be decorated or expose endpoint declarations")


def _service_declares_endpoints(service):
    """Recognize endpoint-bearing services without invoking their handlers."""
    if isinstance(service, Endpoint):
        return True
    if callable(service) and getattr(service, "agent_endpoint", None) is not None:
        return True
    if callable(getattr(service, "endpoint_declarations", None)):
        return True
    return any(
        getattr(member, "agent_endpoint", None) is not None
        for _, member in inspect.getmembers(type(service))
    )


def _resolve_plugin_extensions(plan, context):
    names = {extension.name for extension in plan.extensions}
    extensions = {}
    for plugin_name in _plugin_dependency_order(plan.plugins):
        plugin = plan.plugins[plugin_name]
        plugin_runtime = plugin.runtime_extension(context)
        if plugin_runtime is None:
            continue
        for extension in plugin_runtime:
            if extension.name in names:
                raise ValueError(f"duplicate plugin runtime extension: {extension.name}")
            names.add(extension.name)
        extensions[plugin.name] = plugin_runtime
    dependency_order(
        _extension_dependencies(plan, extensions), kind="application extension",
    )
    return extensions


def _extension_dependencies(plan, plugin_extensions):
    """One startup DAG for application and plugin-owned server components."""
    all_extensions = (
        *plan.extensions,
        *(extension for group in plugin_extensions.values() for extension in group),
    )
    names = {extension.name for extension in all_extensions}
    extension_owners = {
        extension.name: plugin_name
        for plugin_name, group in plugin_extensions.items()
        for extension in group
    }

    def direct_dependencies(extension):
        return (
            *extension.requires,
            *(name for name in extension.optional_requires if name in names),
        )

    exports = {
        capability.name: name
        for name, plugin in plan.plugins.items()
        for capability in plugin.capabilities
    }
    dependencies = {
        extension.name: direct_dependencies(extension)
        for extension in plan.extensions
    }
    for name, group in plugin_extensions.items():
        providers = tuple(dict.fromkeys(
            exports[requirement.name]
            for requirement in (
                *plan.plugins[name].requires,
                *plan.plugins[name].optional_requires,
            )
            if requirement.name in exports
        ))
        provider_extensions = tuple(
            extension.name
            for provider in providers
            for extension in plugin_extensions.get(provider, ())
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
        for capability in plugin.capabilities
    }
    return dependency_order({
        name: tuple(
            exports[requirement.name]
            for requirement in (*plugin.requires, *plugin.optional_requires)
            if requirement.name in exports
        )
        for name, plugin in plugins.items()
    }, kind="plugin")


def _construct_extension_service(extension, services):
    service = extension.service
    if extension.service_factory is not None:
        dependencies = {
            dependency: services[dependency]
            for dependency in (*extension.requires, *extension.optional_requires)
            if dependency in services
        }
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
    return Extension({
        **extension,
        "service": service,
        "service_factory": None,
        "endpoints": endpoints,
        "websockets": websockets,
    })


def build_application(
    application: AgentApplication,
    *,
    context: BuildContext | None = None,
) -> FastAPI:
    """Compile the complete declaration, then build its FastAPI runtime."""
    if not isinstance(application, AgentApplication):
        raise TypeError("build_application expects an AgentApplication")
    plan = application.compile()
    build_context = context if context is not None else BuildContext()
    object.__setattr__(build_context, "_capabilities", plan.capabilities)
    plugin_extensions = _resolve_plugin_extensions(plan, build_context)
    declarations = {
        extension.name: extension
        for extension in (
            *plan.extensions,
            *(item for group in plugin_extensions.values() for item in group),
        )
    }
    extension_dependencies = _extension_dependencies(plan, plugin_extensions)
    startup_order = dependency_order(extension_dependencies, kind="application extension")
    resolved_services = {}
    resolved_extensions = {}
    for name in startup_order:
        extension = _construct_extension_service(declarations[name], resolved_services)
        resolved_extensions[name] = extension
        if extension.service is not None:
            resolved_services[name] = extension.service
    resolved_plugin_extensions = {
        plugin_name: tuple(
            resolved_extensions[name]
            for name in startup_order
            if name in {extension.name for extension in group}
        )
        for plugin_name, group in plugin_extensions.items()
    }
    plan = ApplicationPlan({**plan,
        "extensions": tuple(
            resolved_extensions[extension.name]
            for extension in plan.extensions
        ),
        "plugin_extensions": MappingProxyType(resolved_plugin_extensions),
        "extension_order": tuple(startup_order),
    })
    runtime = ApplicationContext(plan)

    services = []
    for name in startup_order:
        extension = resolved_extensions[name]
        if extension.service is None:
            if any((extension.start, extension.stop, extension.health)):
                raise ValueError(
                    f"extension {extension.name} declares lifecycle hooks without a service"
                )
            continue
        dependencies = tuple(
            dependency for dependency in extension_dependencies[name]
            if runtime.extensions[dependency].service is not None
        )
        services.append(ServiceSpec(
            name=extension.name,
            service=extension.service,
            depends_on=dependencies,
            start=extension.start,
            stop=extension.stop,
            health=extension.health,
            critical=extension.critical,
        ))
    graph = ServiceGraph(services, context=runtime.services)
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
        for plugin_name, group in plan.plugin_extensions.items()
        for extension in group
    }
    all_extensions = tuple(
        (runtime.extensions[name], plugin_owners.get(name, name))
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
            for declaration in _declared_endpoints(source):
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
        for endpoint in _declared_endpoints(PluginHostApi(runtime)):
            registry.add(endpoint, owner="plugin_host")
    if application.surfaces:
        if application.security is None:
            raise ValueError("client surfaces require a security policy")
        for endpoint in _declared_endpoints(SurfaceApi(runtime)):
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
            notify=build_context.get("surface_notify"),
            progress=build_context.get("surface_progress"),
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
    plugin_host = PluginHost(
        plan,
        state_path=build_context.get("plugin_state_path"),
        binding_update=build_context.get("plugin_binding_update"),
        binding_snapshot=build_context.get("plugin_binding_snapshot"),
    )
    runtime.plugin_host = plugin_host

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
    "AgentApplication",
    "ApplicationPlan",
    "BuildContext",
    "Capability",
    "CapabilityRequirement",
    "ClientSurface",
    "Extension",
    "Plugin",
    "build_application",
]
