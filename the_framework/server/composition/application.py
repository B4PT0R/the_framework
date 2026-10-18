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
from typing import Any, Literal
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from modict import modict

from ..api.declarations import Endpoint
from ...agent.extensions.extension import AgentExtension
from ...agent.spec import AgentSpec
from ...agent.context.session import Session
from ...utils.ids import timestamp_id
from ...agent.extensions.specialists import AgentTrigger

from ..api.endpoints import EndpointRegistry, SecurityPolicy, endpoint_declarations
from ..api.plugins import PluginHostApi
from .plugins import PluginHost
from .controls import _bind_application_controls
from ..runtime.session import SessionRuntime
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
            )
        extensions = _tuple(runtime)
        if not all(isinstance(item, ServerExtension) for item in extensions):
            raise TypeError(f"plugin {self.name} did not produce ServerExtension declarations")
        return extensions

class ApplicationPlan(modict):
    """Validated immutable output of application compilation."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    application: Application
    primary_agent: AgentSpec | None
    server_extensions: tuple[ServerExtension, ...]
    surfaces: tuple[ClientSurface, ...]
    agents: Mapping[str, AgentSpec]
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
    primary_agent: AgentSpec | None = None
    agents: tuple[AgentSpec, ...] = ()
    plugins: tuple[Plugin, ...] = ()
    server_extensions: tuple[ServerExtension, ...] = ()
    surfaces: tuple[ClientSurface, ...] = ()
    security: SecurityPolicy | None = None
    title: str | None = None
    description: str | None = None
    docs_url: str | None = "/docs"
    validate_responses: bool = True

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
    primary_session = modict.attr(None)
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
        if not self.name.strip():
            raise ValueError("application name is required")
        if not self.version.strip():
            raise ValueError("application version is required")
        if self.agents and self.primary_agent is None:
            raise ValueError("additional agents require a primary_agent")

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
        return _tuple(value) if key in {"agents", "plugins", "server_extensions", "surfaces"} else value

    def compile(self):
        return _compile_application(self)

    def _build_server(self, context: BuildContext | None = None):
        return _assemble_server(self, context=context)

    def build(
        self, reference: str | None = None, context: BuildContext | None = None, *,
        security: SecurityPolicy | None = None,
        handshake: Callable | None = None,
        event_handshake: Callable | None = None,
        application_handshake: Callable | None = None,
        supervisor: object | None = None,
        session_factory: Callable | None = None,
        session_projection: str = "full",
        health_projection: Callable | None = None,
        reset_handler: Callable | None = None,
        session_extensions: tuple[ServerExtension, ...] | Callable = (),
    ):
        """Configure the unique application server without starting its resources.

        The ASGI lifespan starts/stops services and owns the process-local current
        application. Workers may import/compile this object without starting a server.
        Each additional session owns a SessionRuntime, not another Application.
        """
        if _current_application is self:
            raise RuntimeError("cannot rebuild the running application")
        if self.primary_agent is None:
            if reference is not None or any(value is not None for value in (
                handshake, event_handshake, application_handshake, supervisor,
                session_factory, health_projection, reset_handler,
            )) or session_extensions:
                raise ValueError("conversation inputs require a primary_agent")
            return _assemble_server(self, context=context, security=security)
        if reference is None:
            raise ValueError("an application with a primary_agent requires its module:application reference")
        if context is None:
            raise ValueError("an application with a primary_agent requires BuildContext(data_root=...)")
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

        def build_session(path, agent_name, session_id, durable, *, primary=False, worker=None):
            declarations = {agent.name: agent for agent in (self.primary_agent, *self.agents)}
            if agent_name not in declarations:
                raise ValueError(f"unknown application agent: {agent_name}")
            if primary:
                inputs = context
            else:
                values = dict(context)
                values.update(session_path=path, session_id=session_id,
                              session_mode="durable" if durable else "resident",
                              session_owner=self)
                values["plugins"] = {name: dict(options) for name, options in context._plugin_options.items()}
                inputs = BuildContext(values)
            return self._build_session_server(
                reference, inputs, agent_name=agent_name, primary=primary,
                security=security, event_handshake=event_handshake,
                application_handshake=application_handshake, supervisor=worker,
                session_factory=session_factory, session_projection=session_projection,
                health_projection=health_projection, reset_handler=reset_handler,
                session_extensions=session_extensions,
            )

        app = build_session(context.require("session_path"), self.primary_agent.name,
                            context.get("session_id"), True, primary=True, worker=supervisor)
        self.session_root = Path(context.require("data_root")) / "sessions"
        self.event_handshake = event_handshake
        self.session_builder = build_session
        from ..api.sessions import SessionsApi
        registry = EndpointRegistry(app, security=security, validate_responses=self.validate_responses)
        for endpoint in endpoint_declarations(SessionsApi(self)):
            registry.add(endpoint, owner="sessions")
        registry.install()
        app.mount("/sessions", self.serve_session, name="sessions")
        return app

    def _build_session_server(
        self, reference, context, *, agent_name, primary, security,
        event_handshake, application_handshake, supervisor, session_factory,
        session_projection, health_projection, reset_handler, session_extensions,
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
                session_mode=context.get("session_mode"), session_id=context.get("session_id"),
            )
        else:
            path = getattr(supervisor, "session_path", None)
            if path is not None and Path(path).expanduser().resolve() != Path(context.require("session_path")).expanduser().resolve():
                raise ValueError("supervisor and build context must use the same session")
            worker_reference = getattr(supervisor, "application_reference", None)
            if worker_reference is not None and worker_reference != reference:
                raise ValueError("supervisor and conversation must use the same application reference")
        runtime = (session_factory(supervisor, context) if session_factory is not None
                   else SessionRuntime(supervisor, restart=context.get("restart")))
        if not primary and runtime is self.primary_session:
            raise ValueError("session_factory must create an independent runtime for each session")
        extra = session_extensions(runtime) if callable(session_extensions) else session_extensions
        return _assemble_server(
            self, context=context, security=security, session=runtime, primary=primary,
            extra_extensions=(
                *_conversation_extensions(
                    runtime, data_root=context.require("data_root"),
                    event_handshake=event_handshake, application_handshake=application_handshake,
                    agent_api=CanonicalAgentApi(runtime, session_projection=session_projection,
                        reset_handler=(lambda: reset_handler(runtime)) if reset_handler else None),
                    health_api=ApplicationHealthApi(health_projection, runtime=runtime),
                ), *_tuple(extra),
            ),
        )

    def service(self, name):
        return self.services.require(name)

    def session_runtimes(self):
        if self.primary_session is not None:
            yield self.primary_session
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
        if self.primary_session is not None and self.primary_session.ready is not None:
            runtime = self.primary_session
            records[runtime.ready.session_id] = {
                "id": runtime.ready.session_id, "agent": runtime.ready.name,
                "durable": Path(runtime.supervisor.session_path).is_file(),
                "running": runtime.supervisor.running, "default": True,
            }
        if self.session_root is not None and self.session_root.exists():
            for path in sorted(self.session_root.glob("*/session.json")):
                session = Session.open(path)
                records[path.parent.name] = {"id": session.id, "agent": session.agent_name,
                                             "durable": True, "running": False}
        for key, runtime in active.items():
            records[key] = {"id": key, "agent": runtime.ready.name,
                            "durable": (self.session_root / key / "session.json").is_file(),
                            "running": runtime.supervisor.running}
        return list(records.values())

    async def open_session(self, *, session_id=None, agent=None, durable=True):
        """Open one independent agent worker; its persisted session owns its state."""
        if self.session_builder is None:
            raise RuntimeError("session hosting is not configured")
        key = session_id or timestamp_id()
        if not isinstance(key, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", key) is None:
            raise ValueError("invalid session id")
        async with self.session_lock:
            if not self.accepting_sessions:
                raise RuntimeError("application session host is not running")
            if self.primary_session is not None and self.primary_session.ready is not None and key == self.primary_session.ready.session_id:
                if agent is not None and agent != self.primary_session.ready.name:
                    raise ValueError("session belongs to another agent")
                return self.primary_session
            if key in self.session_apps:
                app, _ = self.session_apps[key]
                runtime = app.state.session
                if agent is not None and runtime.ready.name != agent:
                    raise ValueError("session belongs to another agent")
                return runtime
            path = self.session_root / key / "session.json"
            if path.exists():
                saved = Session.open(path)
                if not durable or (agent is not None and saved.agent_name != agent):
                    raise ValueError("session identity or persistence mode differs")
                agent = saved.agent_name
            else:
                agent = agent or self.primary_agent.name
            created = not path.exists()
            app = self.session_builder(path, agent, key, durable)
            runtime = app.state.session
            if runtime is self.primary_session:
                raise ValueError("session_factory must create an independent runtime for each session")
            if durable and created:
                session = Session(id=key, agent_name=agent)
                session.set_attr("path", path)
                session.save()
            lifespan = app.router.lifespan_context(app)
            try:
                await lifespan.__aenter__()
            except BaseException:
                if created:
                    shutil.rmtree(path.parent, ignore_errors=True)
                raise
            self.session_apps[key] = (app, lifespan)
            return app.state.session

    async def close_session(self, session_id):
        """Stop a worker; durable session data remains available for reopening."""
        async with self.session_lock:
            item = self.session_apps.pop(session_id, None)
            if item is None:
                return False
            app, lifespan = item
            await lifespan.__aexit__(None, None, None)
            if not (self.session_root / session_id / "session.json").exists():
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
            authenticated = await self.event_handshake(WebSocket(scope, receive, send))
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
        default = self.primary_session is not None and self.primary_session.ready is not None and key == self.primary_session.ready.session_id
        if re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", key) is None or (
            not default and key not in self.session_apps and not (self.session_root / key / "session.json").is_file()
        ):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"error": "session_not_found"}, status_code=404)(scope, receive, send)
            return
        await self.open_session(session_id=key)
        app = self.app if default else self.session_apps[key][0]
        scope = {**scope, "root_path": scope.get("root_path", "") + "/" + key}
        await app(scope, receive, send)


def _compile_application(application, *, extra_extensions=(), surfaces=None):
    plugins = {}
    capabilities = {}
    extensions = [*application.server_extensions, *extra_extensions]
    surfaces = application.surfaces if surfaces is None else surfaces
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
            if (application.primary_agent is not None and identity == application.primary_agent.name) or identity in private_identities:
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
    primary_agent = None
    if application.primary_agent is not None:
        primary_agent = application.primary_agent.with_extensions(
            *agent_contributions
        ).with_specialist_profiles(*specialist_profiles)
        agents[primary_agent.name] = primary_agent
    for declaration in application.agents:
        if declaration.name in agents:
            raise ValueError(f"duplicate agent identity: {declaration.name}")
        agents[declaration.name] = declaration.with_extensions(*agent_contributions).with_specialist_profiles(*specialist_profiles)
    return ApplicationPlan(
        application=application,
        primary_agent=primary_agent,
        server_extensions=tuple(extensions), surfaces=tuple(surfaces),
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
    names = {extension.name for extension in plan.server_extensions}
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
    security=None, extra_extensions=(), session=None, primary=False,
) -> FastAPI:
    """Compile the complete declaration, then build its FastAPI runtime."""
    if not isinstance(application, Application):
        raise TypeError("server assembly expects an Application")
    if _current_application is application and (session is None or primary):
        raise RuntimeError("cannot rebuild the running application")
    is_root = session is None or primary
    surfaces = application.surfaces if is_root else ()
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
        application.primary_session = application.app = None
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
    startup_order = dependency_order(extension_dependencies, kind="application extension")
    resolved_services = {}
    resolved_extensions = {}
    construction_errors = {}
    for name in startup_order:
        declaration = declarations[name]
        owner = build_context.get("session_owner")
        if owner is not None and declaration.scope == "application":
            shared = owner.extensions.get(name)
            if shared is None:
                raise ValueError(f"application service is unavailable: {name}")
            if name in owner.service_graph.errors:
                construction_errors[name] = owner.service_graph.errors[name]
            declaration = ServerExtension({
                **shared, "endpoints": (), "websockets": (), "mounts": (), "middleware": (),
                "start": lambda *_: None, "stop": lambda *_: None,
            })
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
        session_runtimes = [service for service in resolved_services.values() if isinstance(service, SessionRuntime)]
        if len(session_runtimes) > 1:
            raise ValueError("an application may install only one primary agent runtime")
        application.primary_session = session if session is not None else session_runtimes[0] if session_runtimes else None
        session = application.primary_session
    if session is not None:
        session.services = build_context._services
    agent_runtime = session if isinstance(session, SessionRuntime) else None

    services = []
    for name in startup_order:
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
        services, context=runtime.services, construction_errors=construction_errors,
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
    app.state.application = application
    app.state.session = session
    runtime.app = app
    plugin_owners = {
        extension.name: plugin_name
        for plugin_name, group in plugin_server_extensions.items()
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
                for surface in surfaces
                for route in (*surface.routes, surface.preview_route)
            ):
                raise ValueError(
                    f"client surface route collides with application mount: {path}"
                )
            mounts.append((path, mounted_app, name))
    mount_prefixes = tuple(path for path, _, _ in mounts)
    surface_prefixes = tuple(
        route
        for surface in surfaces
        for route in (*surface.routes, surface.preview_route)
    )
    allowed_surface_routes = {
        route
        for surface in surfaces
        for route in surface.server_routes
    }
    registry = EndpointRegistry(
        app,
        security=policy,
        validate_responses=application.validate_responses,
        application=application,
        reserved_prefixes=mount_prefixes,
    )
    socket_registry = WebSocketRegistry(app, security=policy)
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
    if plan.plugins and policy is not None:
        for endpoint in endpoint_declarations(PluginHostApi(runtime)):
            registry.add(endpoint, owner="plugin_host")
    if surfaces:
        if policy is None:
            raise ValueError("client surfaces require a security policy")
        for endpoint in endpoint_declarations(SurfaceApi(runtime)):
            registry.add(endpoint, owner="surface_service")
    registry.install()
    socket_registry.install()
    for middleware, options in middleware_entries:
        app.add_middleware(middleware, **options)
    for path, mounted_app, name in mounts:
        app.mount(path, mounted_app, name=name)
    if surfaces:
        surface_root = build_context.get("surface_root")
        if surface_root is None:
            raise ValueError("client surfaces require surface_root in BuildContext")
        surface_service = SurfaceService(
            surfaces,
            surface_root,
            origin=build_context.get("origin"),
            notify=build_context.get("surface_notify", runtime.surface_notify),
            progress=build_context.get("surface_progress", runtime.surface_progress),
        )
        runtime.surface_service = surface_service
        for surface in surfaces:
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
                    security=policy,
                ),
                name=f"surface-preview:{surface.name}",
            )
    binding_updates = build_context.contributions("plugins.binding_update")
    if len(binding_updates) > 1:
        raise ValueError("only one plugin binding coordinator may be installed")
    plugin_host = PluginHost(
        plan, service_graph=graph, service_owners=MappingProxyType(plugin_owners),
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
                await plugin_host.start()
                if is_root:
                    application.accepting_sessions = True
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
