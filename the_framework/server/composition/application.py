"""Declarative composition for complete agent applications."""

from __future__ import annotations

import inspect
import asyncio
import re
import shutil
from importlib import import_module
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from modict import modict

from ..api.declarations import Endpoint
from ..api.uploads import MAX_TOTAL_UPLOAD_BYTES
from ...agent.extensions.extension import AgentExtension
from ...agent.spec import AgentSpec
from ...agent.context.session import Session
from ...utils.ids import timestamp_id
from ...agent.extensions.specialists import AgentTrigger

from ..api.endpoints import EndpointRegistry, SecurityPolicy, endpoint_declarations
from .plugins import PluginHost
from .controls import _bind_application_controls
from ..runtime.session import SessionRuntime
from .dependencies import dependency_order
from .services import ServiceContext, ServiceGraph
from .declarations import (
    BuildContext, Capability, CapabilityRequirement, Plugin, ServerExtension, _tuple,
)
from .routes import _install_routes, _paths_overlap, _path_within_mount
from ..clients.surfaces import ClientSurface


class ApplicationPlan(modict):
    """Validated immutable output of application compilation."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    application: Application
    server_extensions: tuple[ServerExtension, ...]
    surfaces: tuple[ClientSurface, ...]
    agents: Mapping[str, AgentSpec]
    specialists: tuple[AgentTrigger, ...]
    plugins: Mapping[str, Plugin]
    capabilities: Mapping[str, Capability]


_current_application = None
_application_lock = Lock()


class Application(modict):
    """Global application configuration and server resource owner.

    Imports and compilation are inert. build() constructs the ASGI adapter; its
    lifespan reserves current(), starts resources and releases them on shutdown.
    Concurrent conversations own SessionRuntime objects, never Application copies.
    """

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    name: str
    version: str
    agents: tuple[AgentSpec, ...] = ()
    plugins: tuple[Plugin, ...] = ()
    server_extensions: tuple[ServerExtension, ...] = ()
    surfaces: tuple[ClientSurface, ...] | None = None
    security: SecurityPolicy | None = None
    title: str | None = None
    description: str | None = None
    docs_url: str | None = "/docs"
    validate_responses: bool = True
    max_upload_bytes: int | None = MAX_TOTAL_UPLOAD_BYTES

    plan = modict.attr(None)
    context = modict.attr(None)
    services = modict.attr(None)
    extensions = modict.attr(None)
    access_policy = modict.attr(None)
    service_graph = modict.attr(None)
    plugin_host = modict.attr(None)
    surface_service = modict.attr(None)
    session_root = modict.attr(None)
    session_builder = modict.attr(None)
    session_lock = modict.attr(None)
    session_apps = modict.attr(None)
    accepting_sessions = modict.attr(False)
    event_handshake = modict.attr(None)
    application_handshake = modict.attr(None)
    default_session = modict.attr(None)
    default_session_id = modict.attr(None)
    app = modict.attr(None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.services = ServiceContext()
        self.session_apps = {}
        self.session_lock = asyncio.Lock()

    def __setattr__(self, name, value):
        if name in type(self).__attributes__:
            self.set_attr(name, value)
        else:
            super().__setattr__(name, value)

    @classmethod
    def current(cls):
        """Return the process's running application, including during startup."""
        application = _current_application
        if application is None:
            raise RuntimeError("no application is running")
        return application

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if self.max_upload_bytes is not None and self.max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive or None")
        if not self.name.strip():
            raise ValueError("application name is required")
        if not self.version.strip():
            raise ValueError("application version is required")

    @modict.any_validator(mode="before")
    def normalize_sequences(self, key, value):
        if key == "surfaces" and value is None:
            return None
        if key == "plugins":
            plugins = []
            for declaration in _tuple(value):
                if isinstance(declaration, type) and issubclass(declaration, AgentExtension):
                    if not declaration.name:
                        raise ValueError("a plugin class declaration requires an explicit name")
                    declaration = Plugin(name=declaration.name, agent_extension=declaration)
                plugins.append(declaration)
            return tuple(plugins)
        return _tuple(value) if key in {"agents", "plugins", "server_extensions", "surfaces"} else value

    def compile(self):
        return _compile_application(self)

    def build(
        self, reference: str | None = None, context: BuildContext | None = None, *,
        security: SecurityPolicy | None = None,
        handshake: Callable | None = None,
        event_handshake: Callable | None = None,
        application_handshake: Callable | None = None,
        supervisor: object | None = None,
        agent: str | None = None,
        session_factory: Callable | None = None,
        session_projection: str = "display",
        health_projection: Callable | None = None,
        session_extensions: tuple[ServerExtension, ...] | Callable = (),
    ):
        """Configure the unique application server without starting its resources.

        The ASGI lifespan starts/stops services and owns the process-local current
        application. Workers may import/compile this object without starting a server.
        Every session owns its SessionRuntime, independently of the application services.
        """
        if _current_application is self:
            raise RuntimeError("cannot rebuild the running application")
        if not self.agents:
            if reference is not None or any(value is not None for value in (
                handshake, event_handshake, application_handshake, supervisor,
                session_factory, health_projection, agent,
            )) or session_extensions:
                raise ValueError("conversation inputs require declared agents")
            return _assemble_server(self, context=context, security=security)
        if reference is None:
            raise ValueError("an application with agents requires its module:application reference")
        if context is None:
            raise ValueError("an application with agents requires BuildContext(data_root=...)")
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
        surfaces = self.surfaces
        if surfaces is None:
            # The explicit worker reference also locates editable client sources;
            # never infer application paths from cwd or the Python call stack.
            source_file = getattr(import_module(module), "__file__", None)
            source = Path(source_file).resolve().parent / "ui" if source_file else None
            if source is None or not source.is_dir():
                raise ValueError(
                    "default desktop UI requires ui/ beside the application module; "
                    "declare custom surfaces or use surfaces=() for no UI"
                )
            surfaces = (ClientSurface(source=source),)

        path = Path(context.require("session_path"))
        selected = self._select_agent(agent, path=path)
        saved = Session.open(path) if path.exists() else None
        default_id = saved.id if saved is not None else context.get("session_id") or timestamp_id()

        def build_session(path, agent_name, session_id, durable, *, retain_history=None, worker=None):
            self._select_agent(agent_name)
            if path.exists():
                saved_retention = Session.open(path).retain_history
                if retain_history is not None and retain_history != saved_retention:
                    raise ValueError("session history policy differs from saved session")
                retain_history = saved_retention
            elif retain_history is None:
                retain_history = True
            values = dict(context)
            values.update(session_path=path, session_id=session_id,
                          durable=durable, retain_history=retain_history, session_owner=self)
            values["plugins"] = {name: dict(options) for name, options in context._plugin_options.items()}
            return self._build_session_server(
                reference, BuildContext(values), agent_name=agent_name,
                security=security, event_handshake=event_handshake,
                application_handshake=application_handshake, supervisor=worker,
                session_factory=session_factory, session_projection=session_projection,
                health_projection=health_projection, session_extensions=session_extensions,
            )

        app = _assemble_server(self, context=context, security=security,
                               surfaces=surfaces, application_only=True)
        self.session_root = Path(context.require("data_root")) / "sessions"
        self.event_handshake = event_handshake
        self.application_handshake = application_handshake
        self.session_builder = build_session
        self.default_session_id = default_id
        from ..api.sessions import SessionsApi
        registry = EndpointRegistry(app, security=security, validate_responses=self.validate_responses)
        for endpoint in endpoint_declarations(SessionsApi(self)):
            registry.add(endpoint, owner="sessions")
        registry.install()
        app.mount("/sessions", self.serve_session, name="sessions")
        default_app = build_session(path, selected, default_id, True, worker=supervisor)
        from fastapi.routing import APIRoute
        from starlette.routing import Mount, Route
        from functools import partial
        shared_routes = {(route.path, method) for route in app.routes if isinstance(route, APIRoute)
                         for method in route.methods}
        for route in default_app.routes:
            if isinstance(route, Mount) and any(
                isinstance(shared, Mount) and _paths_overlap(route.path, shared.path) for shared in app.routes
            ):
                raise ValueError(f"session mount conflicts with an application mount: {route.path}")
            if not isinstance(route, APIRoute):
                continue
            if any((route.path, method) in shared_routes for method in route.methods):
                raise ValueError(f"session endpoint conflicts with application endpoint: {route.path}")
            for mount in tuple(app.routes):
                if isinstance(mount, Mount) and _path_within_mount(route.path, mount.path):
                    allowed = {path for surface in surfaces for path in surface.server_routes}
                    if route.path not in allowed:
                        raise ValueError(f"session endpoint is shadowed by an application mount: {route.path}")
                    app.router.routes.insert(0, Route(route.path, partial(self.serve_default_session), methods=route.methods))
        original_openapi = app.openapi

        def openapi():
            shared = original_openapi()
            local = self.session_apps.get(self.default_session_id, (default_app, None))[0].openapi()
            components = {**local.get("components", {}), **shared.get("components", {})}
            components["schemas"] = {**local.get("components", {}).get("schemas", {}),
                                     **shared.get("components", {}).get("schemas", {})}
            return {**shared, "paths": {**local["paths"], **shared["paths"]}, "components": components}

        app.openapi = openapi
        self.default_session = default_app.state.session
        self.session_apps[default_id] = (default_app, None)
        app.mount("/", self.serve_default_session, name="default_session")
        return app

    def _select_agent(self, agent=None, *, path=None):
        names = {spec.name for spec in self.agents}
        if path is not None and path.exists():
            saved = Session.open(path).agent_name
            if saved is not None:
                if agent is not None and agent != saved:
                    raise ValueError("session belongs to another agent")
                agent = saved
        if agent is None:
            if len(names) != 1:
                raise ValueError("select an agent explicitly when several agents are declared")
            agent = next(iter(names))
        if agent not in names:
            raise ValueError(f"unknown application agent: {agent}")
        return agent

    def _build_session_server(
        self, reference, context, *, agent_name, security,
        event_handshake, application_handshake, supervisor, session_factory,
        session_projection, health_projection, session_extensions,
    ):
        from ..runtime.supervisor import WorkerSupervisor
        from ..api.agent import CanonicalAgentApi
        from ..api.health import ApplicationHealthApi
        from .runtime import _conversation_extensions

        if supervisor is None:
            supervisor = WorkerSupervisor(
                context.require("session_path"), application_reference=reference,
                agent_name=agent_name,
                application_root=context.get("machine_root", context.require("data_root")),
                durable=context.get("durable"), retain_history=context.get("retain_history"),
                session_id=context.get("session_id"),
            )
        else:
            path = getattr(supervisor, "session_path", None)
            if path is not None and Path(path).expanduser().resolve() != Path(context.require("session_path")).expanduser().resolve():
                raise ValueError("supervisor and build context must use the same session")
            worker_reference = getattr(supervisor, "application_reference", None)
            if worker_reference is not None and worker_reference != reference:
                raise ValueError("supervisor and conversation must use the same application reference")
        if context.get("validation", False):
            if not isinstance(supervisor, WorkerSupervisor):
                raise ValueError("image validation requires the standard WorkerSupervisor")
            supervisor.validation = True
        runtime = (session_factory(supervisor, context) if session_factory is not None
                   else SessionRuntime(supervisor, restart=context.get("restart")))
        if any(runtime is item.state.session for item, _ in self.session_apps.values()):
            raise ValueError("session_factory must create an independent runtime for each session")
        extra = session_extensions(runtime) if callable(session_extensions) else session_extensions
        return _assemble_server(
            self, context=context, security=security, session=runtime, extra_extensions=(
                *_conversation_extensions(
                    runtime, data_root=context.require("data_root"),
                    event_handshake=event_handshake, application_handshake=application_handshake,
                    agent_api=CanonicalAgentApi(runtime, session_projection=session_projection),
                    health_api=ApplicationHealthApi(health_projection, runtime=runtime),
                ), *_tuple(extra),
            ),
        )

    def service(self, name):
        return self.services.require(name)

    def session_runtimes(self):
        for app, _ in tuple(self.session_apps.values()):
            yield app.state.session

    async def surface_notify(self, payload):
        for runtime in self.session_runtimes():
            await runtime.surface_notify(payload)

    async def surface_progress(self, payload):
        for runtime in self.session_runtimes():
            await runtime.surface_progress(payload)

    def list_sessions(self):
        active = {key: app.state.session for key, (app, _) in self.session_apps.items()}
        records = {}
        path = self.context.get("session_path") if self.context is not None else None
        if path is not None and Path(path).is_file() and self.default_session_id is not None:
            saved = Session.open(path)
            records[self.default_session_id] = {"id": saved.id, "agent": saved.agent_name,
                                                "durable": True, "running": False, "default": True}
        if self.session_root is not None and self.session_root.exists():
            for path in sorted(self.session_root.glob("*/session.json")):
                session = Session.open(path)
                records[path.parent.name] = {"id": session.id, "agent": session.agent_name,
                                             "durable": True, "running": False,
                                             **({"default": True} if session.id == self.default_session_id else {})}
        for key, runtime in active.items():
            if runtime.ready is None:
                continue
            path = self.context.require("session_path") if key == self.default_session_id else self.session_root / key / "session.json"
            records[key] = {"id": key, "agent": runtime.ready.name,
                            "durable": Path(path).is_file(), "running": runtime.supervisor.running,
                            **({"default": True} if key == self.default_session_id else {})}
        return list(records.values())

    async def open_session(self, *, session_id=None, agent=None, durable=True, retain_history=None):
        """Open one independent agent worker; its persisted session owns its state."""
        if self.session_builder is None:
            raise RuntimeError("session hosting is not configured")
        key = session_id or timestamp_id()
        if not isinstance(key, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", key) is None:
            raise ValueError("invalid session id")
        async with self.session_lock:
            if not self.accepting_sessions:
                raise RuntimeError("application session host is not running")
            pending = self.session_apps.get(key)
            if pending is not None:
                app, lifespan = pending
                runtime = app.state.session
                if lifespan is not None:
                    if agent is not None and runtime.ready.name != agent:
                        raise ValueError("session belongs to another agent")
                    if retain_history is not None and retain_history != runtime.ready.retain_history:
                        raise ValueError("session history policy differs from running session")
                    return runtime
            path = (Path(self.context.require("session_path")) if key == self.default_session_id
                    else self.session_root / key / "session.json")
            if path.exists() and not durable:
                raise ValueError("session identity or persistence mode differs")
            created = not path.exists()
            if pending is None:
                agent = self._select_agent(agent, path=path)
                app = self.session_builder(path, agent, key, durable, retain_history=retain_history)
            elif agent is not None:
                self._select_agent(agent, path=path)
            lifespan = app.router.lifespan_context(app)
            try:
                await lifespan.__aenter__()
            except BaseException:
                self.session_apps.pop(key, None)
                if key == self.default_session_id:
                    self.default_session = None
                if created and key != self.default_session_id:
                    shutil.rmtree(path.parent, ignore_errors=True)
                raise
            self.session_apps[key] = (app, lifespan)
            if key == self.default_session_id:
                self.default_session = app.state.session
            return app.state.session

    async def close_session(self, session_id):
        """Stop a worker; durable session data remains available for reopening."""
        async with self.session_lock:
            item = self.session_apps.pop(session_id, None)
            if item is None:
                return False
            app, lifespan = item
            if session_id == self.default_session_id:
                self.default_session = None
            if lifespan is not None:
                await lifespan.__aexit__(None, None, None)
            if session_id != self.default_session_id and not (self.session_root / session_id / "session.json").exists():
                shutil.rmtree(self.session_root / session_id, ignore_errors=True)
            return True

    async def close_sessions(self):
        errors = []
        for key in tuple(self.session_apps):
            try:
                await self.close_session(key)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("session shutdown failed", errors)

    async def serve_session(self, scope, receive, send):
        """Dispatch a session's API/sockets without sharing its worker or services."""
        from starlette.requests import Request
        from starlette.websockets import WebSocket
        from starlette.responses import JSONResponse

        relative = scope["path"][len(scope.get("root_path", "")):].lstrip("/")
        key = relative.split("/", 1)[0]
        if scope["type"] == "websocket":
            handshake = self.application_handshake if scope["path"].endswith("/api/v1/application") else self.event_handshake
            authenticated = await handshake(WebSocket(scope, receive, send))
        else:
            request = Request(scope, receive)
            principal = await self.access_policy.authenticate(request)
            authenticated = principal is not None and await self.access_policy.authorize(
                principal, {"scope": "session:read"}, request,
            )
        if not authenticated:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        default = key == self.default_session_id
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", key) is None or (
            not default and key not in self.session_apps and not (self.session_root / key / "session.json").is_file()
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"error": "session_not_found"}, status_code=404)(scope, receive, send)
            return
        await self.open_session(session_id=key)
        app = self.session_apps[key][0]
        scope = {**scope, "root_path": scope.get("root_path", "") + "/" + key}
        await app(scope, receive, send)


    async def serve_default_session(self, scope, receive, send):
        from starlette.responses import JSONResponse
        entry = self.session_apps.get(self.default_session_id)
        if entry is None:
            if scope["type"] == "websocket":
                from starlette.websockets import WebSocket
                handshake = self.application_handshake if scope["path"].endswith("/api/v1/application") else self.event_handshake
                authenticated = await handshake(WebSocket(scope, receive, send))
                await send({"type": "websocket.close", "code": 1013 if authenticated else 1008})
            else:
                from starlette.requests import Request
                principal = await self.access_policy.authenticate(Request(scope, receive))
                status = 503 if principal is not None else 401
                await JSONResponse({"error": "default_session_closed" if principal is not None else "unauthorized"},
                                   status_code=status)(scope, receive, send)
            return
        await entry[0](scope, receive, send)


def _compile_application(application, *, extra_extensions=(), surfaces=None):
    plugins = {}
    capabilities = {}
    extensions = [*application.server_extensions, *extra_extensions]
    surfaces = (application.surfaces or ()) if surfaces is None else surfaces
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
    specialists = []
    for plugin in application.plugins:
        if not isinstance(plugin, Plugin):
            raise TypeError("application plugins must be Plugin instances")
        if plugin.name in plugins:
            raise ValueError(f"duplicate plugin: {plugin.name}")
        plugins[plugin.name] = plugin
        for invocation in plugin.specialists:
            identity = f"{plugin.name}.{invocation.name}"
            if identity in {spec.name for spec in application.agents} or identity in private_identities:
                raise ValueError(f"duplicate private agent identity: {identity}")
            private_identities.add(identity)
            compiled_agent = AgentSpec({**invocation.spec, "name": identity})
            agents[identity] = compiled_agent
            specialists.append(AgentTrigger({**invocation, "name": identity, "spec": compiled_agent}))
        for capability in plugin.provides_capabilities:
            if capability.name in capabilities:
                raise ValueError(f"duplicate plugin capability: {capability.name}")
            capabilities[capability.name] = capability
        # A worker must be able to compile the agent projection without
        # constructing server-only resources or needing server credentials.
        static_extensions = plugin.server_extensions
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
        route for surface in surfaces
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
    for declaration in application.agents:
        if declaration.name in agents:
            raise ValueError(f"duplicate agent identity: {declaration.name}")
        agents[declaration.name] = declaration.with_extensions(*agent_contributions)
    return ApplicationPlan(
        application=application,
        server_extensions=tuple(extensions), surfaces=tuple(surfaces),
        agents=MappingProxyType(agents), specialists=tuple(specialists),
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
    names = {extension.name for extension in plan.server_extensions}
    extensions = {}
    for plugin_name in _plugin_dependency_order(plan.plugins):
        plugin = plan.plugins[plugin_name]
        plugin_runtime = plugin.build_server_extensions(context)
        for extension in plugin_runtime:
            if extension.name in names:
                raise ValueError(f"duplicate plugin runtime extension: {extension.name}")
            names.add(extension.name)
        extensions[plugin.name] = plugin_runtime
    return extensions


def _extension_dependencies(plan, plugin_server_extensions):
    """One startup DAG for application and plugin-owned server components."""
    all_extensions = (
        *plan.server_extensions,
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
        for extension in plan.server_extensions
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


def _unavailable_extension(extension):
    """Retain lifecycle identity/dependencies without exposing an absent service."""
    return ServerExtension({
        **{key: extension[key] for key in (
            "name", "scope", "critical", "requires_services", "optional_services",
        )},
        "endpoints": (), "websockets": (),
    })


def _construct_extension_service(extension, services, construction_errors, context):
    service = extension.service
    if extension.service_factory is not None:
        dependencies = {
            dependency: services[dependency]
            for dependency in (*extension.requires_services, *extension.optional_services)
            if dependency in services
        }
        signature = inspect.signature(extension.service_factory)
        parameters = signature.parameters
        context_parameter = parameters.get("context")
        if (
            context_parameter is not None
            and context_parameter.kind != context_parameter.VAR_KEYWORD
        ):
            dependencies["context"] = context
        if not any(item.kind == item.VAR_KEYWORD for item in parameters.values()):
            dependencies = {name: value for name, value in dependencies.items() if name in parameters}
        # Argument/declaration errors remain composition errors. Only exceptions
        # from the user's factory body participate in optional-service degradation.
        signature.bind(**dependencies)
        try:
            service = extension.service_factory(**dependencies)
        except Exception as error:
            if extension.critical:
                raise
            construction_errors[extension.name] = str(error)
            return _unavailable_extension(extension)
        if inspect.isawaitable(service):
            if inspect.iscoroutine(service):
                service.close()
            raise TypeError(
                f"extension factory must be synchronous: {extension.name}"
            )
    endpoints = extension.endpoints
    if endpoints is None:
        endpoints = (service,) if service is not None and _service_declares_endpoints(service) else ()
    websockets = extension.websockets
    if websockets is None:
        declarations = getattr(service, "websocket_declarations", None)
        websockets = tuple(declarations()) if callable(declarations) else ()
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


def _assemble_server(
    application: Application,
    *,
    context: BuildContext | None = None,
    security=None, extra_extensions=(), session=None, surfaces=None, application_only=False,
) -> FastAPI:
    """Compile the complete declaration, then build its FastAPI runtime."""
    if not isinstance(application, Application):
        raise TypeError("server assembly expects an Application")
    is_root = context is None or context.get("session_owner") is None
    if _current_application is application and is_root:
        raise RuntimeError("cannot rebuild the running application")
    surfaces = ((application.surfaces or ()) if surfaces is None else surfaces) if is_root else ()
    policy = security if security is not None else application.security
    plan = _compile_application(application, extra_extensions=extra_extensions, surfaces=surfaces)
    build_context = context if context is not None else BuildContext()
    build_context._begin(plan.capabilities, application)
    if is_root:
        application.context = build_context
        application.services = build_context._services
        application.plan = plan
        application.extensions = MappingProxyType({})
        application.service_graph = application.plugin_host = application.surface_service = None
        application.default_session = application.app = None
        application.default_session_id = None
        application.session_apps = {}
        application.access_policy = policy
    plugin_server_extensions = _resolve_plugin_server_extensions(plan, build_context)
    build_context._seal()
    declarations = {
        extension.name: extension
        for extension in (
            *plan.server_extensions,
            *(item for group in plugin_server_extensions.values() for item in group),
        )
    }
    # Resolve lazy factories only in the server process, before dependency ordering.
    for name, declaration in declarations.items():
        if isinstance(declaration.service_factory, str):
            module, attribute = declaration.service_factory.split(":", 1)
            declarations[name] = ServerExtension({
                **declaration, "service_factory": getattr(import_module(module), attribute),
            })
    plugin_server_extensions = {
        name: tuple(declarations[item.name] for item in group)
        for name, group in plugin_server_extensions.items()
    }
    # Application declarations also need their inferred dependencies after import.
    plan = ApplicationPlan({**plan, "server_extensions": tuple(
        declarations[item.name] for item in plan.server_extensions
    )})
    extension_dependencies = _extension_dependencies(plan, plugin_server_extensions)
    if application_only:
        shared_names = {name for name, declaration in declarations.items() if declaration.scope == "application"}
        for name in shared_names:
            if any(dependency not in shared_names for dependency in extension_dependencies[name]):
                raise ValueError(f"application service {name} cannot depend on a session or unknown service")
        startup_order = dependency_order(
            {name: extension_dependencies[name] for name in declarations if name in shared_names},
            kind="application extension",
        )
    else:
        startup_order = dependency_order(extension_dependencies, kind="application extension")
    owner = build_context.get("session_owner")
    if owner is not None:
        build_context._services.parent = owner.service_graph
    resolved_services = {}
    resolved_extensions = {}
    construction_errors = {}
    inherited = set()
    for name in startup_order:
        declaration = declarations[name]
        if owner is not None and declaration.scope == "application":
            shared = owner.extensions.get(name)
            if shared is None:
                raise ValueError(f"application service is unavailable: {name}")
            if name in owner.service_graph.errors:
                construction_errors[name] = owner.service_graph.errors[name]
            inherited.add(name)
            resolved_extensions[name] = shared
            if shared.service is not None:
                resolved_services[name] = shared.service
            continue
        unavailable = [dependency for dependency in extension_dependencies[name]
                       if dependency in construction_errors]
        if unavailable:
            error = RuntimeError("application service dependencies are unavailable: " + ", ".join(unavailable))
            if declaration.critical:
                raise error
            construction_errors[name] = str(error)
        if name in construction_errors:
            extension = _unavailable_extension(declaration)
        else:
            extension = _construct_extension_service(declaration, resolved_services, construction_errors, build_context)
        resolved_extensions[name] = extension
        if extension.service is not None:
            resolved_services[name] = extension.service
    runtime = application if is_root else session
    runtime.plan = plan
    runtime.context = build_context
    runtime.services = build_context._services
    runtime.extensions = MappingProxyType(resolved_extensions)
    runtime.surface_service = None if is_root else application.surface_service
    if is_root:
        application.access_policy = policy
        application.session_root = application.session_builder = application.event_handshake = None
        application.session_lock = asyncio.Lock()
    if session is not None:
        session.services = build_context._services
    agent_runtime = session if isinstance(session, SessionRuntime) else None

    services = []
    for name in startup_order:
        if name in inherited:
            continue
        extension = resolved_extensions[name]
        if extension.service is None and name not in construction_errors:
            if any((extension.start, extension.stop, extension.health)):
                raise ValueError(
                    f"extension {extension.name} declares lifecycle hooks without a service"
                )
            continue
        services.append(extension)
    service_names = {extension.name for extension in services}
    graph = ServiceGraph(
        services, context=runtime.services, construction_errors={
            name: error for name, error in construction_errors.items() if name not in inherited
        },
        dependencies={
            extension.name: tuple(
                dependency for dependency in extension_dependencies[extension.name]
                if dependency in service_names or (dependency in inherited and dependency in owner.service_graph.specs)
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
    app.state.application = application
    app.state.session = session
    runtime.app = app
    plugin_owners = {
        extension.name: plugin_name
        for plugin_name, group in plugin_server_extensions.items()
        for extension in group if extension.name in resolved_extensions
    }
    all_extensions = tuple(
        (runtime.extensions[name], plugin_owners.get(name, name))
        for name in startup_order if name not in inherited
    )
    _install_routes(app, runtime, all_extensions, application_only=application_only)
    binding_updates = build_context.contributions("plugins.binding_update")
    if len(binding_updates) > 1:
        raise ValueError("only one plugin binding coordinator may be installed")
    plugin_host = None if application_only else PluginHost(
        plan, service_graph=graph, service_owners=MappingProxyType({
            name: plugin for name, plugin in plugin_owners.items()
            if resolved_extensions[name].service is not None or name in construction_errors
        }),
        binding_update=build_context.get("plugin_binding_update", binding_updates[0] if binding_updates else agent_runtime.set_plugin_binding if agent_runtime else None),
        binding_snapshot=build_context.get("plugin_binding_snapshot", agent_runtime.plugin_bindings if agent_runtime else None),
    )
    runtime.plugin_host = plugin_host
    if session is not None:
        session.application = application
        session.context = build_context
        session.extensions = runtime.extensions
        session.service_graph = graph
        session.plugin_host = plugin_host
        session.surface_service = application.surface_service
    if agent_runtime is not None:
        _bind_application_controls(agent_runtime, app, build_context)

    @asynccontextmanager
    async def lifespan(_app):
        global _current_application
        if is_root:
            with _application_lock:
                if _current_application is not None:
                    raise RuntimeError("an application is already running in this process")
                _current_application = application
        try:
            if is_root and runtime.surface_service is not None:
                await runtime.surface_service.initialize(ui_root=build_context.get("ui_root"))
            await graph.start()
            try:
                if plugin_host is not None:
                    await plugin_host.start()
                if is_root:
                    application.accepting_sessions = True
                    if application.default_session_id is not None:
                        await application.open_session(session_id=application.default_session_id)
                yield
            finally:
                if is_root:
                    application.accepting_sessions = False
                try:
                    if is_root:
                        await application.close_sessions()
                finally:
                    await graph.stop()
        finally:
            if is_root:
                with _application_lock:
                    _current_application = None

    app.router.lifespan_context = lifespan
    return app


__all__ = [
    "Application",
    "ApplicationPlan",
    "BuildContext",
    "Capability",
    "CapabilityRequirement",
    "ClientSurface",
    "ServerExtension",
    "Plugin",
]
