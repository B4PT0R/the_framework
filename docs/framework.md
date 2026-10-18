# The Framework — composition and runtime

All reusable Python code lives under one namespace, `the_framework`:

- `the_framework.agent`: persistent agent, worker, payloads and plugin contracts;
- `the_framework.server`: declarative server composition, supervisors and transports;
- `the_framework.plugins`: optional general-purpose capabilities and their resources;
- `the_framework.utils`: owner-neutral identifiers, token counting and atomic persistence.

`the_harness` consumes this framework and owns product identity, policies and
domain-specific plugins. Dependencies point from the application to the core,
never back. Shared TypeScript workspaces remain separate browser-side packages.

For a usable starting application rather than API fragments, see the
[Local Agent starter](../starter/README.md): neutral chat/settings, general plugins
including memory and voice, a persistent Chromium shell, and an editable UI with
build/preview/publish/rollback. Its composition and interface are ordinary
application source, not a mandatory core frontend.

The root package exposes the declaration decorators together: `tool`, `provider`,
`command`, `hook`, `agent_trigger`, `endpoint` and `websocket`. Specialized runtime
controllers and wire payloads remain available through their dedicated modules.

## Responsibility map

Modules are grouped by responsibility, not by implementation size:

- `agent.models`: payloads, configuration, events and state records;
- `agent.context`: canonical history, context construction, projections and
  compaction;
- `agent.extensions`: agent contributions, tools, providers, hooks and
  specialist contracts;
- `agent.runtime`: execution loops, worker protocol and process entrypoint;
- `agent.spec`: the public agent construction declaration;
- `server.composition`: application declarations, dependency validation and
  plugin/service installation;
- `server.api`: HTTP/WebSocket declarations, discovery, authorization and adaptation;
- `server.runtime`: application/worker supervision, bridges and discovery;
- `plugins.realtime`: voice projection, transport and delegation orchestration;
- `server.clients`: browser shells, client surfaces, remote pairing and Tailscale.
- `utils`: small primitives shared by agents, plugins and the server.

`utils.ids`, `utils.tokens` and `utils.persistence` are small shared framework primitives. They do
not belong to an agent execution loop or server owner. General plugins retain
their existing capability-oriented packages under `plugins/`.

The `the_framework`, `the_framework.agent` and `the_framework.server` facades retain
the concise public construction API. Internal imports target the owning module directly; the
subpackages introduce no forwarding services or legacy module aliases.

The worker entrypoint is `python -m the_framework.agent.runtime.worker_process`.
Runtime prompt resources live next to that runtime and are verified from the
built wheel. Moving Python modules does not change persisted JSON discriminators
or the HTTP/WebSocket protocol.

This repository distributes the `the_framework` import package as `b4pt0r-the-framework` and bundles the neutral `starter`
template, without the product application. Run `the-framework bootstrap` (or
`python -m the_framework bootstrap`) to copy the editable starter into a code
directory while keeping its private data in a separate directory. The package
is published on PyPI as `b4pt0r-the-framework`. There are no legacy top-level `agent`, `server`
or `agent_plugins` import aliases. The wheel isolation test extracts only the
`the_framework` package, blocks product imports, and constructs the general
plugins using their packaged Markdown resources.

`Application` is the application's global object. It carries both its declared
configuration and its server-owned resources: service graph, plugin host, client
surfaces, primary conversation and additional session lifespans. The separate
ApplicationHost layer has been removed. Declare `application = Application(...)`
in an importable module and build its server through `application.build(...)`.
The ASGI server's `state.application` is that same object.

Constructing or compiling Application does not construct services, launch workers,
open a browser or start a server. Worker processes import and compile this module
without activating an application server. Python globals are process-local; the
server's live resources are not shared with those worker processes.

`build(...)` constructs the ASGI server and resolves service factories. With a
primary agent it installs conversation, authenticated text/attachment routes,
voice coordination when installed, and independent session hosting. Without an
agent it installs only declared services and surfaces. An agent application must
provide its importable reference, storage inputs and authentication; omission does
not silently build a service-only server. Building does not run Uvicorn or enter
the ASGI lifespan.

### Application and session lifetime

| Object | What it owns |
| --- | --- |
| `Application` | The unique running server's configuration, services, surfaces, plugin host and session management. |
| `SessionRuntime` | One conversation's supervisor, specialist fleet, commands, event subscribers, capability bridge and session-scoped services/routes. Every concurrent session has its own controller, linked to the same Application. |
| `Session` | Canonical history, configuration, extension state and plugin bindings, in memory or atomically persisted by its worker. |
| `BuildContext` | Fixed construction inputs and plugin options for the application or one session; its read-only service view fills during startup. `context.application` points to the owning Application. |

The ASGI lifespan reserves the process-global application before starting services.
`Application.current()` returns it during service startup, request handling and
shutdown. A second active application is rejected without replacing the first.
The reservation is released after shutdown, including startup failure or
cancellation. A running Application cannot be rebuilt in place; supervised server
restart reconstructs resources and resumes the canonical sessions.

Outside that lifecycle, `Application.current()` raises `RuntimeError`. During
service construction use the explicit `context.application` instead:

```python
# A plugin factory called by build():
def services(context):
    application = context.application
    # Read context inputs/options here; services have not started yet.

# Inside a running service or request handler:
application = Application.current()
```

`application.context` holds the root build inputs; `application.plan` holds the
compiled assembly. `application.security` is the optional declared access policy;
`application.access_policy` is the effective policy selected during build.
The agent owns one `Session` and one `Context`. `Session` keeps canonical data
and atomic mutations; `Context` prepares model input and orchestrates compaction.
Applications do not assemble a separate compaction controller.

- `agent.context.input()` / `ainput()` prepare the canonical history with its
  dynamic prefix, then add ephemeral providers for the current generation.
- `Context.project(items, max_input_images=..., ephemeral=...)` converts a supplied
  sequence to Responses input without changing or saving a session. Image bytes
  are encoded transiently; payload models keep their existing conversion logic.
- Preparing canonical input repairs interrupted tool sequences and prunes missing
  local references through `Session.sanitize()` before projection. Pure projection
  alone does not perform that recovery or write to disk.
- `agent.compact()` compacts the current canonical context using the same preparation
  as generation, without sampling/including ephemeral providers. The Context calls
  the backend and emits lifecycle events; `Session.commit_compaction()` atomically
  updates the active history, archive and anchor. There is no separate runtime
  `Compaction` object or arbitrary `input` override.
- UI history comes from the retained archive and named `SessionProjections`;
  Realtime owns its voice projection. They do not reuse Responses input as a
  display format. The item model `Compaction` and `CompactionResponse` payload
  remain distinct from runtime operations and keep their schemas.

Declaration fields such as `plugins` and `server_extensions` keep their input
shape. `application.extensions` is the read-only map of resolved server declarations.
`application.service(name)` reads the root service registry, including the primary
session's services. Additional sessions must use their own `session.service(name)`.
`application.primary_session` names the primary controller explicitly.

`await application.open_session(...)`, `application.list_sessions()` and
`await application.close_session(id)` manage conversations. Creating one assembles
its controller and scoped ASGI routes, not another Application or a cloned root
definition. Shared application services are reused; session services, plugin
bindings and health remain scoped to that conversation. `session.application` and
`session.context.application` refer to the global Application. Its ASGI adapter
exposes `state.application` as the global object and `state.session` as its own
controller; request handlers can select this explicit session through
`request.app.state.session`. There is no global mutable current-session pointer.

`compile()` produces a validated `ApplicationPlan` without running factories.
Configuration fields remain in the mapping; live resources use metadata attributes
outside that mapping. Worker compilation therefore cannot capture running services
from the server's Python object. Wire payload models and their schemas are unchanged.

`service_factory=...` provides deferred construction and dependency injection.
Objects supplied directly as `service=...` are used as provided, without cloning.
Application-scoped services are shared across sessions; concurrent workers use
distinct session storage paths. Shutdown closes hosted sessions before stopping
root services in reverse dependency order. The desktop process owner binds/listens
and runs ASGI separately; its persistent browser survives server replacement.

`agent.bridge.call(...)` sends a capability request to its server-side
`session.bridge`. An HTTP handler's `EndpointContext.application` now refers to
the global Application itself. No transport or persistence migration is required.

Application code supplies identity, plugins, domain services, policies and UI
surfaces. The core owns process supervision, ordered commands, lifecycle,
validation, error translation and transport plumbing.

## The small path

A complete HTTP service can be assembled without manipulating FastAPI or a
lifecycle callback:

```python
from the_framework import (
    Application,
    ServerExtension,
    Plugin,
    endpoint,
)


@endpoint("get", "/api/v1/greeting", authenticated=False)
def greeting() -> dict[str, str]:
    return {"message": "hello"}


application = Application(
    name="Example",
    version="1.0",
    plugins=(Plugin(name="greeting", server_extensions=ServerExtension(
        name="greeting_api", endpoints=(greeting,),
    )),),
)

app = application.build()
```

The declaration is immutable. `Application.build()` validates the complete
graph before installing routes: duplicate names and paths, missing or cyclic
dependencies, malformed JSON schemas, missing security policy and duplicate
mounts fail immediately with a precise composition error.

Structured declarations and runtime records use `modict`. Construct them with
named fields or a mapping; runtime declarations can contain non-JSON references
without converting or copying those objects. Browser execution handles live in
attributes outside its configuration mapping. For a coordinated change, rebuild
the complete declaration, for example
`Application({**application, "plugins": plugins, "server_extensions": server_extensions})`:
validation then sees the final state, not intermediate per-key assignments.

## Adding state and lifecycle

HTTP declarations belong to the server: import `endpoint` and `Endpoint` from
`the_framework.server`, or `endpoint` from `the_framework`. Decorated functions
and service methods are discovered directly by `ServerExtension`; no agent
registry or extension activation is required. `AgentExtension` contributes only
to the worker. Existing services that share an implementation with an agent
extension can still be explicit endpoint sources, without loading that extension.

### Model-based endpoint contracts

Without an explicit `request`, `@endpoint` uses the handler's signature and
docstring schema: supported annotations validate JSON inputs and coerce path
and query values, parameters with defaults remain optional, and injected
`context` stays outside the payload. An argument-free handler needs no empty
request schema. For example:

```python
@endpoint("get", "/items/{item_id}", authenticated=False)
def item(item_id: int, limit: int = 25):
    return {"item_id": item_id, "limit": limit}
```

Explicit request/response schemas and models take precedence. Use them for
additional constraints or types outside signature inference. `request={}` is
an explicit permissive schema for a handler accepting dynamic keyword inputs.
Response schemas documented in YAML are used when no explicit response is given.

`@endpoint("post", "/route", request=RequestModel, response=ReplyModel)` accepts
`modict` classes as well as explicit JSON Schema mappings. Request fields are
passed as handler keyword arguments after the usual authentication, transport
parsing and validation. Model construction supplies defaults and applies native
modict validators. Response validation also constructs the declared model.

The OpenAPI adapter projects field hints, requiredness and extra-field policy;
it supports JSON scalars, literals, unions, lists, string-keyed dictionaries and
nested models. It does not execute default factories during registration or
attempt to translate arbitrary Python validators. Such constraints remain enforced
at runtime but are not advertised as JSON Schema constraints. Unsupported or
recursive field hints fail registration and require an explicit schema. Explicit
schemas remain unchanged and are preferable for detailed wire-level constraints.

See `examples/minimal_agent_app.py` for a complete authenticated example. No
Pydantic DTO, model serialization layer or second validation implementation is
introduced; model metadata is adapted only at the HTTP/OpenAPI boundary.

### Primary runtime integration

For a primary agent, `Application.build()` installs one session-scoped
`SessionRuntime`, connects the plugin host's binding updates and startup snapshot
to its worker, and registers agent-facing plugin and installed surface controls. Surface
initialization belongs to the server lifespan and also works without an agent
runtime. There is no separate post-build control-registration call.

A plugin can declare a `plugins.binding_update` contribution for a modality or
policy barrier; at most one such coordinator may be installed. Its callback can
look up its running service through `context.services`. An application can also
supply a fixed `plugin_binding_update` construction input as an explicit override. The callback accepts `(name, enabled, *, call=None)` and should
invoke `runtime.set_plugin_binding(name, enabled, call=call)`. The optional
`ApplicationCall` identifies a tool that is suspended awaiting this operation.
Ordinary HTTP changes have no originating call.

Binding controls have default priority 5 (ordinary commands use 10). They may
execute at an application-call suspension point, including before that call is
sent; other queued commands retain their priority and FIFO ordering. Correlated
binding requests whose tool call is no longer waiting fail instead of becoming
late queued mutations. The worker remains the owner of activation and durable
session writes, and confirms application before the server updates its binding cache.

### Standard conversation assembly

`Application.build(reference, context, ...)` assembles the
supervised primary worker, runtime, agent HTTP API, health endpoint and both
WebSocket transports. The reference is the importable `module:application`
resolved by workers. Authentication stays explicit:

```python
from the_framework import BuildContext

app = application.build(
    "my_app.application:application",
    BuildContext(data_root=data_dir),
    security=security,
    handshake=security.handshake,
)
```

The default runtime derives `session.json` and `surfaces/` from
`data_root`. `handshake` applies to both sockets; `event_handshake` and
`application_handshake` independently override it when their wire protocols differ.
The declared primary agent name is passed to the supervised worker automatically.
The framework owns construction, ordering and cleanup; it does not start inference
or perform network authentication during declaration/build.

Advanced applications can pass `session_factory(supervisor, context)` to specialize the
runtime, or inject a supervisor for another worker executable. An injected
supervisor must refer to the same session and, when declared, application reference.
`session_projection` defaults to `"display"` (user/assistant messages and artifacts);
`"full"` is an explicit opt-in to internal history. `health_projection(payload)`
selects diagnostics. Override the async `SessionRuntime.reset_session()` method
to provide the application's selective reset policy; no callback is needed in
`build()`. The default method is unavailable (HTTP 501), even when a low-level
reset transaction factory exists.
`session_extensions(runtime)` returns application-owned declarations for each
session, such as reset endpoints and access middleware. It is invoked once per
session, alongside `session_factory`; these callbacks must not cache/reuse a live
controller across independent sessions. The starter and Harness use this same path.

A session's service context borrows running application-scoped resources from
the application graph. Only local services belong to its start/stop lifecycle;
closing a session cannot stop shared resources. Health retains the shared service's
original probe and errors, and the lookup never exposes another session's private
services. Build-time configure hooks still run with each session's own inputs;
their contributions are not cached globally.

Set the fixed `restart` input on `BuildContext` for the default runtime's supervised
restart callback. Runtime subclasses may specialize restart and UI notifications
through `restart_server`, `surface_notify` and `surface_progress`. Managed surfaces
use those methods automatically; there is no need to copy publish callbacks into
construction inputs.

### Local desktop process ownership

`the_framework.server.runtime.local.LocalServer` owns one replaceable HTTP
process: instance lease, loopback listener, private discovery descriptor and
credentials. Compose the app inside its context, pass `process.request_restart`
to `BuildContext`, then run `await process.serve(app)`. It returns
`RESTART_EXIT_CODE` (75) after a requested restart and zero after a normal stop.
Discovery and the lease are released even if application construction fails.
Bearer credentials rotate per server process; the browser cookie secret remains
in `runtime/ui-session.key`. The application's security policy validates each
credential in its own channel.

| Owner | Lifetime and responsibility |
| --- | --- |
| Desktop supervisor | Starts/replaces the HTTP process. The starter retains an inherited listening socket; the Harness uses systemd with a fixed configured port. |
| Browser RPC daemon | Owns Chromium contexts, tabs and unsent UI state, independently of HTTP/worker replacement. Its async context closes RPCs and Chromium exactly at daemon shutdown. |
| LocalServer | Owns the current HTTP process, discovery and graceful shutdown. It never closes the desktop browser. |
| Application runtime / worker | Supervision and the canonical durable conversation; the worker remains its sole writer. |

With an inherited listener, the supervisor keeps its own descriptor open while
each server closes its copy. With separately supervised processes, use the same
configured port for every replacement so the existing page can reconnect.
`shutdown_event()` scopes SIGINT/SIGTERM handling to a long-lived desktop owner;
`serve(..., parent_stream=...)` optionally stops a child when its parent pipe closes.
Neither requires a new framework-level supervisor or a process-wide task manager.

A service-only `Application` omits `primary_agent` and uses
`application.build(context=BuildContext(...))` when it needs paths or plugin inputs.
It installs no worker, canonical chat or session-management routes. There is no
second public conversation builder or helper to reproduce the host wiring.

Server services may call `await runtime.update_config(updates, id=command_id)`
and `await runtime.apply_event(name, payload, id=event_id)`. Both wait for worker
command completion and preserve the sole history writer. `update_config` returns
`AgentConfigUpdated`, including `result["config"]` and `restart_required`, or
raises `InvalidWorkerResult` when the required result is missing. A supplied id
must be reused with the same payload when retrying. This does not make a sequence
of separate calls atomic. Ephemeral telemetry still uses `TransientEventRequest`;
`runtime.publish` only broadcasts to connected subscribers.

`ApplicationHealthApi(runtime=runtime)` includes worker readiness, activity,
application-bridge connectivity and specialist status. Service probes use the
existing `ServerExtension.health` callback or `service.health()`. The graph reports
a running service as degraded if its probe raises or returns `status` equal to
`error`, `degraded` or `unhealthy`; critical degradation affects aggregate health.
Optional failures remain visible without degrading the aggregate. A projection
may adapt the response to an application's existing client contract.

`FileResetTransaction(root, paths)` supplies the filesystem part of a reset.
Connect it through `SessionRuntime.reset_transaction_factory`; the application
selects which relative paths to remove, while the runtime pauses reset-dependent
services and restarts its workers. Staging restores original files after a failed
restart, including removing newly created selected paths. Paths cannot overlap
or escape the root, and directory symlinks cannot be traversed. This is an
in-process rollback mechanism, not a recovery journal for host/process crashes.

For distinct actions such as clearing a conversation or only a plugin's memory,
call `await runtime.reset_runtime_state(transaction_factory=...)` with a factory
that receives the canonical session path and returns a reset transaction.
The override applies only to that call; the default factory remains unchanged.
Every recipe uses the same serialized suspension, restart and rollback lifecycle.

Plugins register their own reset participation with
`runtime.register_reset_hooks(suspend=..., resume=...)`. Either callback may be
omitted: a live connection may only close, while a durable service may only reload.
At least one callback is required; both may be synchronous or asynchronous.
Suspensions run in reverse registration order before worker shutdown; resumptions
run in registration order after commit or recovery of the previous runtime.
A failed suspension aborts reset and recovers previously suspended participants.
Realtime owns voice reconciliation and call cleanup through these hooks, so the
application's reset handler need not know whether voice is installed.

Remote services support ordered `add_application_event_listener` /
`remove_application_event_listener` and `add_capabilities_changed_listener` /
`remove_capabilities_changed_listener` subscriptions. Event callbacks run
synchronously after lease validation; authority callbacks may be asynchronous
and are awaited in order. Callback failures propagate. Services should subscribe
at startup and unsubscribe at shutdown. Domain-specific authority reactions stay
in their owning plugins.

### Application calls

`ApplicationBridge.dispatch()` accepts a worker call and executes it independently
of worker reception. Results still return through the correlated application
protocol. Register with `contextual="call"` to receive the complete
`ApplicationCall` as the `call` keyword; `contextual=True` supplies only
`command_id`. A slow asynchronous service must not prevent status replies or call
cancellations from being received. The bridge limits concurrent calls, cancels
running calls on `ApplicationCancel`, and drains them on shutdown or worker
recovery. Service handlers must cooperate with cancellation; synchronous work
that blocks the event loop should use an asynchronous adapter.

Worker-output observers are ordered with reception. Their exceptions are logged
without losing the worker response; observers must not wait for replies from
that same worker.

### Service lifecycle

Use constructor callbacks for permanent service relationships and the existing
`ServerExtension.start`/`stop` adapters for lifecycle-specific integration. A callback
such as a restart barrier is not itself a new service or manager. Keep an absent
capability absent: wrapping a missing restart callback would falsely advertise
restart support and fail only when invoked.

An ordinary service only needs conventional `start()` and `stop()` methods:

```python
library = MediaLibrary(...)

media = ServerExtension(
    name="media",
    service=library,
    endpoints=(browse_media, stream_media),
    requires_services=("runtime",),
)
```

Services start in dependency order and stop in exact reverse order. A partial
startup is rolled back, including cancellation; startup and cleanup failures
are both preserved when rollback fails. A service must release any resources
acquired by its own unsuccessful `start()`. Services with `critical=False` can
fail during factory execution or startup without taking down the application.
The graph health report retains the error. A failed factory contributes no routes,
WebSockets, mounts or middleware; its dependents are unavailable too, and a
critical dependent still aborts composition/startup. An optional dependency that
is installed participates in this ordering; its default is used only when absent.
Construction failures are retried by building a new application, not by restarting
the same service graph. Factories must clean up resources acquired before raising.
Invalid declarations, missing dependencies, incompatible factory signatures and
asynchronous factories remain composition errors regardless of `critical`.
Explicit `start`, `stop` and `health` adapters exist for advanced
or third-party services, but are not required by the common path.
An `@endpoint` method named `start` or `stop` is an HTTP handler, not a
lifecycle hook; provide an explicit adapter if that service also needs startup
or shutdown behavior.

A service that depends on the canonical worker may also need to pause while
that worker's persisted session is reset. Register a `suspend`/`resume` pair on
`SessionRuntime.register_reset_hooks()` when constructing that service.
Normal server startup and shutdown still belong to `ServerExtension` lifecycle;
reset hooks run only around a session reset, suspend in reverse registration
order, and resume after either a successful reset or restoration of the previous
session.

When construction depends on other declared services, use a synchronous factory
or a service class. Its named parameters declare the services to inject: a
parameter without a default is required, and one with a default is optional.
Construction happens before startup, while the live `ServiceContext` receives
each instance only after its successful start:

```python
realtime = ServerExtension(
    name="realtime",
    service_factory=lambda runtime: RealtimeController(runtime),
)
```

For example, `service_factory=ConversationApi` can use a constructor
`__init__(self, runtime, realtime=None)` without repeating either dependency.
A present optional service starts before its consumer; an absent one is not
passed to the factory, preserving the parameter's default. This is a
service-name dependency, distinct from a plugin's versioned public capability
dependency. A parameter named `context` receives this build's `BuildContext`,
not a service. Other parameters still name services; fixed inputs are never
injected by guessing their names or passed implicitly to `**services`.
The name `context` is reserved and cannot identify a service.

A factory or constructor can therefore read paths and options without an enclosing
factory or a closure:

```python
class WeatherService:
    def __init__(self, context, runtime):
        self.root = context.require("data_root")
        self.options = context.plugin("weather")
        self.runtime = runtime

weather = Plugin(name="weather", server_extensions=ServerExtension(
    name="weather", scope="session", service_factory=WeatherService,
))
```

Use `service_factory="your_app.weather.server:WeatherService"` to defer server
imports until assembly. Construction and worker compilation do not import that
module. At server build time, the framework resolves the reference and infers its
service dependencies before ordering construction. The factory must be synchronous;
its returned service owns the usual `start`, `stop` and `health` methods.

Use `requires_services` and `optional_services` for additional ordering
constraints, or to make an input with a default mandatory. Additional names are
not passed to the factory unless it accepts them as named parameters or through
`**services`. Positional-only and `*args` factories are rejected: injection uses
service names, including keyword-only parameters.

The same dependency injection applies to a `ServerExtension` returned by a plugin's
server runtime factory. A plugin requiring another plugin's service must also
declare the corresponding versioned public `CapabilityRequirement`; the
compiler rejects undeclared cross-plugin service edges, and the capability
dependency determines startup order. A required dependency cannot be declared
without its provider; an optional one can be absent. Installed plugin runtimes are fixed for the server
lifetime; removing a provider requires updating the startup declaration and
restarting the server.

A service's decorated `@endpoint` methods and `websocket_declarations()` are
normally discovered on the constructed instance. This is the same rule for a
direct service and one returned by a factory; undecorated methods are never HTTP
routes. Omitting `endpoints` or `websockets` (their default is `None`) enables this
convention. Use `endpoints=()` / `websockets=()` to keep that surface private, or an
explicit tuple to expose selected declarations or another object. Authentication,
route collisions and schema validation still apply before startup.
There is no string discovery flag or separate pre-build step.

Runtime lookup goes through the single application context:

```python
runtime = app.state.application.service("runtime")
```

Domain code should not create additional untyped `app.state` attributes.

## Pairing the worker and server sides of a plugin

`Plugin` owns a complete installed feature. `AgentExtension` contributes to an
agent; `ServerExtension` declares a server component. They are parts of a plugin,
not alternative plugin systems. An agent extension is an executable class,
whereas a server extension is a declaration describing a service and its surface.

| Composition field | Expected contribution |
| --- | --- |
| `Plugin.agent_extension` | Agent extension class, factory or import reference |
| `Plugin.server_extensions` | Explicit `ServerExtension` declarations; a single declaration is normalized to a tuple |
| `Plugin.configure` | Advanced `configure(context)` hook collecting contributions and optionally returning additional `ServerExtension` declarations; an import reference defers server imports |
| `Plugin.specialists` | Private `AgentSpec` declarations |
| `Plugin.provides_capabilities` | Versioned contracts exported by this feature |
| `Plugin.requires_capabilities` / `optional_capabilities` | Required or optional versioned contracts from other plugins |
| `Plugin.agent_binding_enabled` / `agent_binding_required` | Initial and mandatory binding to the primary agent; server installation remains fixed |
| `ServerExtension.requires_services` / `optional_services` | Names of local services supplied to its factory, in dependency order |
| `Application.server_extensions` | Application-wide server infrastructure |
| `AgentSpec.extensions` | Contributions installed in that agent |

The normal import is the complete plugin, not its agent and server parts:

```python
from the_framework.plugins.bash import BASH
from the_framework.plugins.memory import MEMORY
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM

application = Application(
    name="Example", version="1", primary_agent=primary,
    plugins=(BASH, MEMORY, SCHEDULER, SYSTEM),
)
```

Bundled feature packages export `BASH`, `CHROMIUM`, `MEMORY`, `REALTIME`,
`REGISTRY`, `SCHEDULER`, `SYSTEM` and `WEB_SEARCH`. Each is an immutable `Plugin`
declaration containing its complete feature: agent binding, private specialists,
server services, lifecycle and capabilities where applicable. Importing or
compiling these declarations does not import their server implementations.
Scheduler and system services require the application's `runtime` service;
`data_root` in `BuildContext` supplies their default storage directory. Optional
plugin options `scheduler.state_path` and `system.state_path` override the files
through `BuildContext(plugins={"system": {"state_path": path}})`. System restart
uses the runtime's application-owned `restart_server` operation when configured.
The neutral realtime plugin exposes call creation/readiness/stop endpoints and
its controller, with an optional `realtime.client_factory` plugin option.
The browser uses the process-local `agent.application_root`, where a browser RPC
host must be running. This application root stays shared when an agent uses a
separate session directory. `agent.session_root` locates per-session resources,
including those of in-memory agents.
`BrowserService.open_application_ui(url)` opens the application's chosen route;
`application_ui_origins` permits explicitly trusted HTTPS origins in addition to
loopback. Query and fragment values are excluded from Chromium launch arguments
and passed through navigation. The isolated UI target/profile `harness_ui` and
its historical RPC command remain stable for saved sessions and browser processes
that survive server or worker upgrades.
Browser DOM handles are opaque and valid only for their latest snapshot; taking a
new snapshot invalidates all older handles, including hidden/truncated elements.
If an RPC caller disconnects or times out, the daemon cancels its pending operation
instead of leaving an orphan task. Cancellation cannot undo an action that Chromium
has already performed.
Tab closure first uses the ordinary Playwright close path. If Chromium acknowledges
it without destroying the tab during a reload (Playwright issue #42068), the daemon
retries that tab through `close(run_before_unload=True)` and waits for its actual
close event. Each attempt has a five-second deadline. Playwright's default dialog
handling accepts the before-unload prompt during this explicit close. If recovery
also stalls, the caller receives `BrowserError`; the daemon never closes the whole
context or the independent application UI to recover a single tab. A warning marks
the native retry in the browser daemon log without recording page contents/URLs.

Customize a complete declaration explicitly without rebuilding its internals:

```python
required_system = Plugin({**SYSTEM, "agent_binding_required": True})
custom_memory = Plugin({**MEMORY, "agent_extension": MyMemoryExtension})
```

Agent extensions and server factories remain available in `agent.py` and
`server.py` for advanced compositions. Custom plugins should expose the same
complete-declaration pattern, keeping server imports deferred. Agent-side
composition uses `add_extension`, `load_extension`, `extension`,
`activate_extension` and `deactivate_extension`.

Passing an agent extension class directly remains a shorthand for a custom
agent-only plugin. It must declare its `name`; the application normalizes it to
`Plugin` with the same identity and private-agent validation.

`Plugin` groups one feature's agent binding, server components and private
specialists. For a service that needs paths or other services, declare its factory
directly; the framework owns construction, ordering and lifecycle:

```python
images = Plugin(
    name="images", agent_extension=ImageGenerationExtension,
    server_extensions=ServerExtension(
        name="images", scope="session",
        service_factory="your_app.images.server:ImageService",
    ),
)
```

`ImageService.__init__(self, context, runtime)` receives the session's build context
and runtime. A callable factory or class works in place of the import reference
when server-only imports are not a concern. Decorated routes and socket declarations
are discovered on the result. Wrap an already constructed service in
`ServerExtension(name=..., service=...)`; the application still owns its lifecycle.
A bare service is not a plugin declaration.

`Plugin.name` is the shared feature identity. Its agent extension is bound under
that name for configuration, state, tool namespaces, specialists and live
binding controls, without changing the extension class. An `AgentExtension`
used directly by `AgentSpec.extensions` keeps its own name (or class name).
This lets a complete plugin supply the identity while its implementation stays
reusable. An already loaded extension cannot be renamed.

Use `ServerExtension` to defer service construction or specify scope, dependencies,
route selection, middleware, mounts or lifecycle adapters, or when the plugin
owns several server components:

```python
images = Plugin(
    name="images",
    agent_extension=ImageGenerationExtension,
    server_extensions=ServerExtension(
        name="image_api",
        service=image_service,
        endpoints=(image_service,),
        requires_services=("runtime",),
    ),
)
```

A feature can own several server components without splitting its runtime into
unrelated application extensions. Its `server_extensions` contains explicit
`ServerExtension` declarations. For build-time contributions or dynamic assembly,
`configure(context)` runs before service construction and may return additional
declarations or None. It can be a callable or a `"module:configure"` reference.
The hook is distinct from `service_factory(context, named_service, ...)`: it
collects declarations and contributions, and must not construct live services.
Application and plugin services share one dependency graph: components
are constructed and started in dependency order, then stopped in reverse order.
This also lets an application extension depend on a plugin-owned service.
The worker's `compile()` path never
calls the configure hook or service factories. Use an importable reference when
the worker should not even import the server module; the reference is resolved
only by `Application.build(...)`. For example, a media plugin can own a store, an index and
an HTTP API, while the application supplies only process-local objects through
`BuildContext` at server build time. Shared core services still live in
`Application.server_extensions`: for example, the canonical worker supervisor
and the application-wide transport boundary. Optional feature services belong
to their `Plugin`.

Declare a required versioned capability in `Plugin.requires_capabilities`: compilation fails
if its provider is absent or too old. Use `Plugin.optional_capabilities` when an
integration should be enabled only if its provider is installed; a present
provider is version-checked and resolved first, but its absence is valid.
Both forms determine construction and startup order. A plugin runtime factory
can call `context.has_capability("voice.session", min_version=1)` when its
routes or behavior differ according to an optional integration; ordinary
optional service injection uses `ServerExtension.optional_services` directly.
A missing provider returns `False`; a present but incompatible provider fails
compilation before the factory runs.
`BuildContext` is a single-build object with three distinct responsibilities:

- its mapping contains fixed application inputs; it cannot acquire new keys
  during construction;
- `context.plugin(name)` returns an immutable shallow snapshot of that plugin's
  process-local options, supplied with `BuildContext(plugins={name: {...}})`;
- `context.services` is a read-only, live view of successfully started services.
  Services appear after startup and disappear during shutdown or rollback.

Declare service dependencies on `ServerExtension` (or infer them from its
factory's named parameters). Ask for `context` explicitly when construction needs
fixed inputs, plugin options or contributions. Use injected service objects during construction.
Use `context.services` in deferred callbacks that need a service during operation,
including authentication. It does not expose partially constructed services.
Services never overwrite or extend construction inputs. Create a fresh context
for each application/session build; use the same context throughout its construction.
`context.application` references the owning Application even before startup.
Saved agent configuration and plugin state belong to Session, not BuildContext.

Most plugins need only an agent extension and a service declaration. A plugin-level
server builder is needed when it assembles several declarations dynamically or
contributes to another feature's policy before service construction.
Such a builder can call `context.contribute("feature.policy",
contribution)` whether it runs before or after the owner factory. The owner
reads `context.contributions("feature.policy")` in its service factory, after
all plugin runtime factories have declared their contributions. Contributions are
sealed before service construction: contributing from a service factory or after
build raises an error. Contribution names and values are contracts defined by the owning feature; the framework
transports them without interpreting them. These build-time values are
process-local, not agent-session state or a hot runtime registry.

In a larger codebase, keep the `Plugin` declaration in the feature's package
alongside its agent class and server runtime factory. Export that object and
list it in `Application.plugins`; the application assembly then chooses
features without repeating their dependencies or import paths. The starter
keeps small declarations together in `starter/application.py` so the complete
example remains easy to read in one place.

Each declared plugin runtime starts with the server and remains installed until
shutdown. Its agent binding can be enabled or disabled live and persists across
restarts; disabling it removes tools and context from the agent but leaves
routes and services available. A server-only plugin has no binding. To remove
a runtime entirely, remove the plugin from the declaration and restart. A legacy
state file containing `running=false` is rejected with a migration error rather
than silently reactivating a previously stopped runtime.
Public inter-plugin dependencies use versioned `Capability` contracts; private
agents are identified as `plugin.agent` and cannot be depended on directly.

The plugin host reports runtime status separately from each agent's persistent
binding. `running` reflects all plugin-owned services in the started service
context: it is false before startup, after shutdown or if any owned service failed
to start. Plugins without lifecycle-managed services (agent-only or route-only)
have no server service to wait for and remain available. `installed` and `loaded`
refer to the fixed composition, not service health; live probe health is exposed
separately through the health API. Its `binding_available` flag distinguishes server-only plugins from
agent plugins whose binding is currently disabled. Application and plugin
endpoints, WebSockets, middleware and mounts
are installed on one server at startup; route and mount collisions fail before
any service starts. OpenAPI includes the complete fixed endpoint set. Required
bindings, such as the system boundary, cannot be disabled.

Plugin bindings live in `Session.plugins`. The worker commits a binding before
confirming it; the server mirrors the worker snapshot and has no separate binding
store. Restarting that session restores its bindings. Starting a new session uses
the declaration defaults.

### Independent application sessions

`build()` keeps the existing `/api/v1/...` API for the default
session. `Application.agents` optionally declares other selectable agents.
Authenticated consumers can create or reopen sessions with
`POST /api/v1/sessions` (`agent`, optional `session_id`, `durable`, default true),
list them with `GET /api/v1/sessions`, and stop an additional worker with
`DELETE /api/v1/sessions/{id}`. Closing preserves durable session data; the
default session follows the application lifecycle.

The response returns an `api` prefix: `/sessions/{id}/api/v1`. History, settings,
plugin bindings, voice and event/application sockets under this prefix belong to
that session. A request to a known persisted session lazily resumes its worker;
an unknown id never creates a worker implicitly. Persistent sessions live under
`data_root/sessions/{id}/session.json`. Resident sessions disappear on close.
Python hosts use `app.state.application.open_session(...)` and `close_session(id)`.
A filesystem writer lock rejects another worker trying to own the same session.

`ServerExtension.scope="application"` (default) shares one service and lifecycle
across sessions. Its routes remain at the root. `scope="session"` builds an
independent service and routes for each session. The standard conversation,
realtime, scheduler and system services use session scope. Their default private
files are relative to `session_path`; the shared file bucket stays application-wide.
Application-wide desktop/remote bridges keep their application policy; selecting
a session in a custom client requires using that session's API and sockets.
The starter and Harness still show their default conversation.

Every runtime owns its own specialist fleet. `subagent_event` carries `agent`,
`session_id`, `task_id`, caller `command_id` and an activity `event`. This ephemeral
feedback never enters the caller history. Durable results retain their ordered
delivery and acknowledgement contract independently of activity feedback.

## Public event contract

Agent streams, hooks, command sockets, specialist activity and realtime captions
use Framework events. Responses and Live wire events stay inside their adapters.
There is no raw-event passthrough: unknown backend diagnostics and binary chunks
are filtered. Known signals are converted by selecting declared fields. Backend
sequence numbers, logprobs and response envelopes are not public event metadata.

| Event | Meaning |
| --- | --- |
| `agent.turn.*`, `agent.step.*`, `agent.generation.start` | Worker execution lifecycle |
| `agent.generation.end` | `status` (completed/incomplete/interrupted/failed), optional usage and error |
| `agent.output_item.started` | Provisional item identity, role and phase; no history write |
| `agent.text.delta`, `agent.text.done` | Item text: `item_id`, `channel` (output/refusal/reasoning/summary), `part_index`, Framework `sequence`, and delta/text |
| `agent.tool_input.delta`, `agent.tool_input.done` | Streaming tool input, item identity and Framework sequence |
| `agent.remote_tool.status` | Inference-service tool execution: tool, item identity, started/running/completed/failed |
| `agent.response_item.added` | Canonical item, emitted once after the session write succeeds |
| `agent.tool_call.*`, `agent.tool_calls.*` | Execution of local tools and user-facing feedback |
| `agent.compaction.*`, `agent.interrupted`, `agent.task.*` | Context lifecycle, interruption and generic specialist requests/results |
| `agent.config.updated`, `agent.state.updated`, `agent.plugin.binding.updated` | Session-owned settings changes |
| `agent.provider_artifacts.updated` | Current-turn presentations; never canonical history |

The streaming sequence is allocated by the Framework per generation and compared
within an item's stream. Part indices identify text/summary parts. Output text
can be displayed provisionally; reasoning is a separate channel. Only canonical
item events and session pages supply durable chat history. Unknown or partial
image/audio chunks do not become chat presentations: use an explicit Artifact.
`on_event` sees converted Framework events before output items are committed;
returning a transformed `AgentResponseItemAdded` changes the item to commit.
Subscribers see that event only after persistence succeeds. Usage is recorded
internally and exposed on generation completion, without forwarding a response.

`Event.from_dict()` reconstructs declared types and rejects unknown types;
event fields reject undeclared keys. `CommandEvent.event`, `SubagentEvent.event`
and `RealtimeEvent.event` all enforce this boundary. The existing command,
worker and application-control envelopes remain Framework-owned protocols.
`AgentPluginBindingUpdated` names the typed binding event.

### Plugin-owned events

Declare an event where its behavior belongs. The core declares only events needed
for generic worker operation. Events used only by a plugin's feature lifecycle
are declared and emitted in that plugin, without a worker-core branch or central
feature-specific import. For example, in a plugin's `events.py`:

```python
from the_framework.agent import LocalEvent

class WeatherUpdated(LocalEvent):
    type: str = "weather.updated"
    temperature: float

# In the owning plugin:
# self.agent.emit(WeatherUpdated(temperature=18.5))
```

Import that plugin's event declarations in each Python process that reconstructs
them (normally when loading the plugin declaration). Type names follow the
existing dotted/underscored class naming convention. An extension to the event
vocabulary is explicit, not a raw backend catch-all.

The Realtime plugin owns `voice.transcript.updated` (role and cumulative text),
`voice.turn.completed` (role, turn id, transcript and interruption),
`voice.response.state`, `voice.speech.state` and `voice.error`. It also declares
its reconnect and delegation lifecycle events. Clients receive these typed
signals in `RealtimeEvent`; they do not assemble backend transcript fragments.
A completed voice turn clears the transient caption; the worker remains the
only writer of the canonical voice transcript. WebRTC carries client audio and
session negotiation remains an internal transport concern.

## Agent construction

`AgentSpec` declares what an agent is; `AgentResources` supplies the process-local
inputs needed to construct it: an inference client and initial configuration/state
defaults. The session owns subsequent changes and overrides these defaults on resume. It is not another plugin or application
composition layer. Put static defaults directly in `AgentSpec.configuration`.
Use a `resources(session_path)` factory only for process-local inputs such as
loading environment settings or creating an inference client. The generic worker
owns construction. Use explicit resources when constructing an agent directly.

```python
from the_framework.agent import AgentSpec

agent_spec = AgentSpec(
    name="companion",
    description="Owner of the canonical conversation.",
    extensions=(memory, images, tools),
    configuration={"model": "your-model"},
    instructions=(identity_instruction,),
    projections={"mobile": mobile_projection},
    command_middleware=(measure_turns,),
    session_mode='durable',
)

agent = agent_spec.build_agent(session_path)
worker = agent_spec.build_worker(agent, receive, send)
```

The same `AgentSpec` declares a private specialist. Specialists are ephemeral
by default and can opt into `session_mode="resident"` or
`session_mode="durable"`. `QueuePolicy` controls bounded FIFO/latest delivery
and durable task journaling. A plugin owns its specialists and calls them
through `agents.submit(name, prompt)`, which returns a request ID without waiting.
The worker routes results to `AgentTriggers.dispatch()` and the declared handler;
there is no `call()` alias suggesting a synchronous result or a second `publish()`
adapter. Another plugin can reach their capability only through an explicit versioned public
contract. The generic worker entrypoint resolves either the primary agent or a
namespaced specialist from `module:application`.

`@agent_trigger(spec, observes=..., item_kinds=..., conversation_roles=...,
input_item_field=...)` binds an `AgentSpec` to a result handler. The decorator
only selects when and what to observe. Model/plugin settings live in the spec's
`configuration`, alongside its instructions, plugins, session/queue policy and
completion settings. There is no second set of model or queue parameters on the
decorator. The spec's name identifies the specialist; a handler docstring fills
its description when the spec uses the default description.

For a single role, `instructions="Describe the evidence."` normalizes to one
`Instruction`. Use an explicit sequence of `Instruction` objects when names or
scopes matter; both forms use the same construction path.

A plugin that configures its specialist at load time replaces `trigger.spec`
with a revised `AgentSpec`; it does not mutate the shared class declaration.
The emitted worker profile is compiled from that current spec and the trigger's
selection. Agent construction applies declared configuration, then process-local
resource defaults, then resolved launch-profile settings when the worker has one.
Saved Session configuration is restored last. The launch profile is consumed
without retaining a competing source in AgentSpec; executable factories and
initializers stay on the application declaration. An explicit session-mode choice
at the worker entrypoint overrides the launch profile's mode.

Application compilation validates private identities and retains their AgentSpec
declarations in its plan. AgentSpec has no compiled `specialist_profiles` field.
After extension loading, the worker forms one declaration catalogue: each loaded
trigger supplies its configured spec and selection, replacing the corresponding
static declaration; explicit specialists without a trigger remain available.
The catalogue includes inactive bindings so the fleet can suspend or re-enable
execution without losing the configured declaration. Only then are WorkerProfile
payloads produced for WorkerReady. Worker constructors no longer merge two sets
of already serialized profiles.

The serialized specialist profile carries `session_mode` explicitly, preserving
`ephemeral`, `resident` and `durable` across worker construction. Session and
queue policies on `AgentSpec` remain authoritative when compiling a profile,
including after modifying a declaration reconstructed from a profile. The old
internal `persistent` boolean is no longer a declaration parameter.

`AgentSpec` is the public in-process construction recipe; `AgentTrigger` binds a
specialist's task/result handling to its plugin. `WorkerProfile`, under
`agent.models.worker`, is internal serializable fleet metadata, not a second
public declaration API. A profile is not a serializer for Python factories,
initializers or projection callables: application-backed workers resolve the
original namespaced spec. The advanced `build_agent(..., profile=...)` argument is
used at this process boundary to apply resolved settings after resource defaults;
ordinary application declarations do not need it. A profile with another agent's
identity is rejected. Imported role text becomes an ordinary spec instruction;
exporting uses the current instructions rather than the old profile text.

Terminal private-agent results enter a durable outbox only after their terminal
state is persisted. Delivery and acknowledgement are idempotent, so a server or
worker crash can replay the result without losing it or applying it twice.

Middleware has the small async-generator contract
`middleware(agent, command, call_next)`. It adds cross-cutting behavior such as
timing or tracing without subclassing the worker. `SessionProjections` supplies
three reserved standard views: `display` keeps user/assistant messages and
artifacts, `activity` additionally keeps function calls, and `full` keeps all items.
These preserve item order and leave vision-only Image items out of chat.
Applications add pure named projections through `AgentSpec.projections`; select
one with `Application.build(session_projection=...)` when a custom view is needed.
Pagination selects canonical archive turns before projecting their items.
Responses and Realtime model input use separate projections.

## Optional live context

The voice controller owns transport, delegation barriers and ephemeral-context
reconciliation, not knowledge of memory or registry contents. Applications compose
`MemoryLiveContext` and `RegistryLiveContext` from their respective general plugins
when wanted, through `start_context`, `worker_context`, `flush_context` and
`close_context`. Context producers own their caches, revision tracking and tasks;
the controller owns the ordered, token-bounded delivery path. Close producers
before releasing the transport. The memory producer interprets its own
`relevant_memory` XML from the retained `provider_slots`; Realtime does not parse
memory or expose a separate memory-candidate projection. The memory candidates
therefore reflect the provider slots retained after instruction-budget fitting.
The Harness demonstrates this composition beside
its own domain context; no general provider manager is required.

## Shared mechanics, separate ownership

Application and plugin services use one startup graph and one rollback path.
The graph validates dependencies across ownership boundaries, starts providers
before consumers, and stops everything in reverse order. Agent-binding changes
do not restart services or alter server routes.

`atomic_text_writer` shares private temporary-file creation, replacement, file
and directory synchronization, and cleanup. Session, registry, mapping and fleet
stores keep their own serialization, validation and writer ordering. The primitive
is not a locking or multi-file transaction API; a directory-sync failure after
replacement reports uncertain durability rather than pretending to roll back.

## Security and endpoint contracts

Remote client privileges are application-owned: `RemoteClientPolicy()` grants
no scopes, capabilities, or platform-specific privileges. Supply the policy
explicitly to `ClientRemoteService` (which passes it to its credential store).
Routed and default leased capabilities must be declared exclusive capabilities;
invalid policy graphs fail at construction. An application with several installed
features can combine their `RemoteClientPolicy` contributions with
`RemoteClientPolicy.compose(base, feature, ...)` before creating the service.
Independent features can contribute policies through `BuildContext`; the
remote-client owner's service factory composes them after all runtime
declarations have been resolved.
Duplicate capability or event ownership is rejected, and policy changes are
forbidden after service startup. Plugin runtime factories run in capability
dependency order, while collected contributions do not rely on that order.
Paired-client records are not rewritten by policy composition.

Authenticated endpoints require an explicit application `SecurityPolicy`.
There is no implicit allow-all policy in `Application`. The registry:

1. authenticates the request;
2. evaluates the endpoint's authorization requirement;
3. parses and validates path/query/JSON input;
4. calls the handler, sync or async;
5. validates JSON output;
6. translates controlled failures into a stable error envelope.

Native FastAPI/Starlette `Response` objects pass through unchanged for streams,
range responses and downloads. The low-level `PermitAllSecurity` policy exists
only for explicit tests and compatibility adapters.

JSON is the default request encoding. Multipart endpoints opt in explicitly
with `request_encoding="multipart"`; array properties remain arrays even when
the request contains only one file, and normal JSON Schema cardinality limits
are enforced before the handler runs.

WebSocket handlers use the matching `@websocket` declaration and are composed
through `ServerExtension(websockets=(...))` or service discovery above. They share
the same authentication and authorization policy, reject unauthorized upgrades
with close code `1008`, and are collision-checked before any route is installed.

Protocols that authenticate during their own handshake can construct a
`WebSocketEndpoint(..., handshake=...)`. The handshake returns a small
connection context that is injected into the handler, or `None` to reject the
upgrade. This supports versioned subprotocols, replay cursors and private-client
credentials without weakening the ordinary HTTP security policy.
Server-push event handlers must also read the peer's disconnect signal while
waiting for the next event. The framework's local and remote event streams use
one shared routine for this, so an idle disconnected client releases its
subscription promptly instead of lingering until a heartbeat or shutdown.

## Context providers

`@provider(channels=("text",))` contributes ephemeral context only to the named
projections. The declaration is honored both when registering the function with
`agent.providers.add(function)` and when loading it through an `AgentExtension`.
Channels are ordinary names; plugins may introduce their own projections.
Explicit `add(..., channels=...)` values override the decorator, including an
empty sequence. Empty channels exclude the provider from filtered projections;
unfiltered `outputs()` / `aoutputs()` still collect it. Without a channel override,
standalone providers keep the default `text` / `realtime` channels, while extension
providers use the extension's `provider_channels` (by default, `text`).

## Client file bucket

All client-visible files share one tree: `data_root/bucket/`, or `~/TheHarness/bucket/`
for the Harness default installation. Uploads and published images, documents,
audio and videos use this same root. Subfolders may organize projects or resources;
there are no separate storage roots by media type. Private sessions, credentials,
memory, browser profiles and UI releases remain outside this tree.

`FileBucket(data_root)` from `the_framework.server.api.bucket` exposes `.root`,
`.url(path)` and an authenticated `GET /api/v1/bucket/{path:path}` endpoint. The
complete `ARTIFACTS` plugin includes that endpoint; the same files are served to
local clients and authorized remote desktop clients. Nested paths are supported;
directory listing, traversal and symlinks escaping the bucket are rejected.
Delivery supports byte ranges for large media and uses attachment disposition.
Only publish files intended for clients into this tree. There is no migration or
alias for earlier experimental `files/` and `artifacts/` layouts.

## Conversation artifacts

`Artifact` from `the_framework.agent` expresses presentation explicitly:

```python
from the_framework.agent import Artifact, Image

return (
    Image(path=screenshot),  # vision input, invisible in the chat
    Artifact(kind="widget", payload={"widget": "weather", "temperature": 18}),
)
```

Tools and commands can return an artifact directly or among tuple outputs. The
worker commits and emits the same item; session pages and archive retention supply
replay after reconnect, restart and compaction. A repeated artifact id is an
idempotent publication while retained in the archive. Its developer-message API
projection includes the id, kind and payload, confirming canonical presence only,
never successful client rendering. It does not send image pixels to the model.
Use `Image` separately when vision is required.

Providers can return the same typed item, but **all provider outputs are ephemeral**.
On each model step, `agent.provider_artifacts.updated` replaces the current turn's
provider presentations. No provider output is appended to history or archive;
its API projection identifies its temporary scope. Clients discard these views
on turn completion, interruption, failure or connection loss. They are absent
from replay and renewed by a later provider sample. This supports a current
browser screenshot without filling the conversation with successive screenshots.

Add `ARTIFACTS` from `the_framework.plugins.artifacts` to the application's
plugins for local file publication. Its optional `artifacts.show_artifact` tool
accepts `kind="image"` or `"file"` and `payload={"path": "/local/result.png",
"title": "Result", "description": "..."}`. It copies the bytes atomically below
`data_root/bucket` and returns an artifact with `payload.src`, `name`, `size`
and `mime_type`; deleting or overwriting the source cannot change this publication.
Only references and JSON metadata enter the session; neither original nor copied
file bytes are embedded there as base64. The copy lives separately in the served
files. If it disappears, the artifact remains in history and the UI reports the
content as unavailable. Vision inputs are encoded only for the outgoing model
request; that transient API representation is never written back to the session.

The asset route requires authentication and `session:read`; filenames are not
credentials. Assets are bytes, with no second conversation or replay registry.
Image publication validates raster PNG/JPEG/WebP/GIF. Downloads cannot execute
supplied HTML in the application's origin. Assets remain available when the
plugin's agent binding is disabled, and are not automatically deleted on reset.

An extension producing a file can use the same service without a second model
call or an explicit display tool invocation:

```python
payload = await self.agent.bridge.call(
    "artifacts", "publish", {"kind": "image", "payload": {"path": result_path}},
)
return Artifact(kind="image", payload=payload)
```

Returning an artifact with already usable JSON or durable URLs needs no service.
Providers should reference their own current resources; presentation lifetime does
not imply deletion of referenced bytes. File asset cleanup is independent of
session replay. Avoid temporary paths and expiring URLs for durable artifacts.

Both UIs render `image` (`payload.src`, optional `alt`, `title`, `description`) and
`file` (`src`, `name`, optional `title`, `description`). Unknown kinds show expandable
JSON; a failing custom renderer falls back without breaking chat. Widgets are
serializable descriptions, interpreted by application-owned React components.
The Harness exposes `artifactRenderers={{weather: Weather}}` on `AssistantApp`
and `Conversation`; each renderer receives `{kind, payload}`. The starter's `App`
has the same prop for local composition. No supplied HTML or JavaScript is executed.

## Built-in application primitives

Reusable agent plugins live in `the_framework.plugins`, separate from the product's
`the_harness.plugins`: `artifacts`, `bash`, `system`, `registry`, `scheduler`, and
`web_search`. Import each capability directly from its subpackage; importing
one does not load the other plugins or product services. Their instruction
files ship alongside their implementations. Application-specific defaults
(including the shell working directory) belong in application configuration.

`the_framework.plugins.memory` owns storage, retrieval, curation tools and orchestration;
applications specialize `memory.agent.MemoryExtension.curator_instructions`.
Curator evidence tasks require an atomic candidate inventory with canonical source
IDs. Maintenance without new evidence starts with `inventory([], [], [])`, rewrites
existing entries with empty source/candidate lists to inherit their provenance,
and repeats the empty inventory after a final review before `complete_curation`.
This path cannot introduce candidates or add unsourced memories; completion still
requires every entry within its editorial threshold and total volume below capacity.
`the_framework.plugins.browser` accepts an explicit runtime directory and uses
`the_framework.server.clients.browser`/`the_framework.server.clients.browser_rpc` for the persistent Playwright process.
`the_framework.plugins.realtime` provides canonical voice projection with application-owned
provider selection. `the_framework.plugins.realtime.controller.RealtimeController` owns transport and
delegation; plugins contribute independent context producers through factories.
Those producers own their state and must stop and await their tasks in `close`.
Use `await controller.append_context(text)` to deliver live-only context without
adding it to canonical history. `delivery="ordered"` preserves accepted updates;
`delivery="latest", key="source"` replaces queued state for that source;
`delivery="discard"` rejects an update while delivery is busy. The controller
waits for peer readiness, active responses and delegated speech. Its bounded
queue returns `False` when full or when no call is active; `True` means delivered
or queued for this call. `discard_context(key)` invalidates queued state, and
`context_blocked` lets producers postpone expensive retrieval. Closing a call
clears queued updates. These public operations replace private transport calls
and application-owned delivery buffers.
Applications retain their own prompts, defaults and specialized context producers.
No product import is required by these shared mechanisms. Runtime observers use
`register_observer`/`unregister_observer`.

Use `Application.build(..., session_factory=...)` when a session needs a
specialized `SessionRuntime`, as the Harness does. The factory receives its worker
supervisor and build context; return a new controller for every invocation. The
builder still owns the canonical APIs, transport and lifecycle wiring. Do not add
a second runtime service manually. For a custom worker executable, the advanced
`supervisor` input accepts a `WorkerSupervisor(command=...)`; custom commands cannot
be combined with its declaration-derived application/agent inputs.
The default fleet uses the same application reference to build private agents;
no forwarding worker command or fleet factory is needed. Custom fleet factories
remain explicit session-runtime overrides.
The default generic worker persists history, configuration, plugin state and
bindings in its canonical session. Static defaults belong in
`AgentSpec.configuration`; optionally declare a
`resources(session_path) -> AgentResources` factory for process-local configuration,
state and optional inference client. It runs during construction, never during
compilation. Explicit `build_agent(resources=...)` replaces this factory. Saved
session values override initial defaults. Updates commit atomically before live
configuration or state changes; a failed write leaves the live values intact.

`the_framework.utils.persistence.MappingStore` remains useful for separate
application service documents. It is not an alternative agent settings store.

The public `the_framework.server` package exports reusable pieces for common agent apps:

- `CanonicalAgentApi` for status, session pages, configuration, compaction,
  interruption and application-defined atomic reset;
- `ApplicationHealthApi` for aggregate and per-service lifecycle health;
- `CanonicalTransportSockets` for ordered agent events and the application
  capability bridge. `build()` exposes it as the
  `canonical_transport` service. Its `event_connection_count` counts accepted
  canonical event sockets, separately from internal stream consumers, the
  application bridge and paired remote connections. Connectivity does not imply
  window visibility or user attention; product presence policy belongs to the app;
- `Application`, `ServerExtension`, `Plugin`, `WebSocketEndpoint` and
  `Application.build()` for composition.

## Versioned client surfaces

`ClientSurface` makes a mutable web template an application capability rather
than a special Vite side effect:

```python
from the_framework import ClientSurface

surface = ClientSurface(
    name="main",
    source=project_root,
    build=("npm", "run", "build:surface"),
    artifact="desktop/.surface-build",
    routes=("/ui", "/remote-ui", "/quest-ui"),
    server_routes=("/ui/bootstrap",),
    shell="playwright",
)
```

Declare one `ClientSurface` per client interface when devices need different
applications: desktop, mobile and Quest can have separate sources, commands,
artifacts and routes. Every release operation takes its surface name; the system
plugin defaults to `main` only for convenience. Updating or rolling back one
surface leaves the others unchanged. Device selection and client-specific
capabilities remain application policy; all surfaces may share the same server
and durable conversation.

The surface service copies sources into an isolated workspace, executes the
fixed argv without a shell, verifies the artifact, hashes it and creates an
immutable release. Candidates are served on authenticated preview URLs.
Publication atomically swaps the persisted active release and notifies clients;
rollback swaps back without rebuilding. Build success is evidence only: the
agent remains responsible for inspecting the preview before publication.

The initial startup path uses the same declaration: if no published release is
available, the server adopts `source / artifact` during its lifespan. An external
packaged main bundle may be supplied with `BuildContext(ui_root=...)`. This only
seeds an unpublished surface; it never replaces an existing selected release.
Missing initial bundles leave the surface at HTTP 503 while the server and
conversation remain available for repair. If a selected release has disappeared,
an intact previous release takes precedence over any source bundle.

Active, previous and candidate selections persist together. Restarting preserves
an unpublished preview and never promotes it. Failed builds preserve all three
selections. New release directories become visible only after a complete copy;
incomplete staging directories are neither previews nor published releases.

The base chat UI has no deployment controls. The system plugin exposes
`system.surfaces`, `system.build_surface`, `system.preview_surface`,
`system.publish_surface` and `system.rollback_surface`. The preview tool opens a
new browser web tab and returns only surface/release metadata, keeping temporary
credentials out of conversation history. It needs `BuildContext(origin=...)`
and the local browser RPC service. Without an explicit release, it opens the
candidate or the active release.
HTTP controls remain available to integrations. Publication and rollback notify
connected clients to refresh; conversation history comes from the durable worker
session. A backend restart alone does not navigate the existing browser page.
`server_routes` explicitly reserves routes beneath a surface mount for an
application or plugin endpoint, such as a login/bootstrap exchange. Other
endpoints and WebSockets under the mounted path are rejected at build time.

One principal desktop owns the application bridge and primary event connection.
A second primary event connection is rejected with code 1013. Authenticated
previews receive concurrent event streams without claiming desktop ownership or
suppressing desktop notifications. They cannot open the application bridge.
The chat shows a read-only badge: drafts and local navigation remain testable,
but prompts, voice, configuration writes and deployment actions are denied.
Previewing never publishes a release or navigates the principal page.

Integrations can request `POST /api/v1/surfaces/{name}/preview` with an optional
`release` to obtain a one-use link valid for 30 seconds. Opening that link on the
configured origin sets an HttpOnly, SameSite=Strict preview cookie valid for eight
hours, then redirects to the selected release. Its private signing key persists
across server replacement; unconsumed links do not. Preview credentials carry
`*:read` and `surfaces:preview`, and HTTP access is limited to GET/HEAD API and
preview routes. The initial link and preview HTML disable caching and referrers.

The starter and Harness integrate the shared `preview_access`, `is_preview` and
`authorize_preview` helpers into their own Host/Origin and authorization policies.
Custom applications must do the same when exposing previews: validate the preview
cookie before ordinary desktop credentials, enforce safe HTTP methods and read
scopes, and supply `client_role="preview"` on the event handshake. Device-specific
mobile/Quest authority remains application policy; a preview grants no such role.

Middleware and mounted ASGI applications belong on a `ServerExtension`, so they are
validated and installed with the rest of the graph, whether that extension is
declared directly by the application or owned by a plugin. Mounted ASGI apps
handle their own request authentication; the framework's `@endpoint` security
policy does not automatically wrap arbitrary mounts. Product code should not
add routes, middleware or mounts after `Application.build()`.

## Standalone example

[`examples/minimal_agent_app.py`](../examples/minimal_agent_app.py) is a second,
independent application that imports only `the_framework`. It demonstrates an
authenticated endpoint, a lifecycle-managed service and graph-derived health
without importing any The Harness product module:

```bash
uvicorn examples.minimal_agent_app:app
```

## The Harness as a larger application example

The separate The Harness application uses the same `Application` and
`Plugin` API as the starter. Its declaration supplies one primary agent,
product plugins, their versioned dependencies and a multi-device client
surface. The server entrypoint supplies process-local inputs through
`BuildContext` and calls `build`, which owns the canonical runtime
assembly.
Each installed plugin constructs its own services and routes through the shared
dependency graph: for example, the Realtime plugin owns its controller-facing
APIs and shared call state, while paired-client transport and media services
belong to their respective plugins. Removing a required capability fails
compilation before the server starts.

This division is useful when building a larger application: the top-level
assembly names the features and provides resources, while each feature owns its
HTTP, WebSocket, service and agent contributions. The framework does not import
application-specific policy.

## Design rule

The framework follows progressive disclosure:

- **simple:** `Application`, `ServerExtension`, `Plugin`, conventional
  service lifecycle;
- **composable:** capabilities, dependencies, decorated endpoint objects,
  middleware, mounts, named projections and plugin specifications;
- **advanced:** explicit lifecycle adapters, authorization policies and custom
  response objects.

Adding power must not make the small path more ceremonial. An application
feature belongs in the core only when it is a reusable orchestration primitive;
identity, prompting, device semantics, media policy and product UX remain in
the application's extensions.

## Canonical chat and optional live context

For an application declaring a primary agent, `Application.build()` installs the host-owned `chat` service,
including `POST /api/v1/agent/prompt` and `POST /api/v1/agent/attachments`.
It is part of the conversation assembly, absent from the plugin catalogue, and
cannot be disabled through plugin controls. A client can omit the chat UI while
keeping these APIs available. An application without a declared primary agent
has no conversation services.
Both accept a client-generated command `id`; attachments use multipart form data
with `files` and an optional `prompt`. The service coordinates with the installed
Realtime API's lock, so attachment acceptance and voice startup cannot race.
`BuildContext.data_root` owns storage; uploads use its shared `bucket/` tree.
No voice plugin is required for text-only apps.

Other client transports inject the host-owned `chat` service into their factory,
then reuse `chat.ingress.submit(PromptRequest(...))`. Do not inspect voice state
and submit directly: selection and acceptance must hold the same lock as voice
startup, shutdown and binding changes. The operation returns `{"id": ..., "route":
"text" | "realtime"}`; in voice mode the id is the voice turn id. HTTP contracts,
authorization and per-client idempotent receipts remain owned by the caller.
The local prompt endpoint still returns its client-generated id.

`ChatIngress` is also available from `the_framework.server.runtime.ingress` for
non-HTTP applications. Construct it with a runtime and, optionally, the installed
realtime service (controller and transition lock). Without voice it uses an
optional `text_lock`, or its own lock. Share one ingress between transports.
Voice accepts ordinary user prompts only: input items, suppressed prompts,
custom roles/kinds and per-command reasoning overrides raise `ValueError` rather
than silently losing text-only semantics. Voice submission failures also raise
`ValueError`; callers choose their transport error representation. Cancellation
and failures release the barrier. Attachment storage continues to hold this same
barrier until its ordered text command is accepted.

A feature that produces live context can contribute a factory through
`the_framework.plugins.realtime.context.CONTEXT_FACTORY` in its server assembly.
The factory receives the voice controller. Contexts implement `start(projection)`
synchronously and optionally `ready()`, `flush()`, `handle_worker_output(output)`
and `close()`; those optional hooks may be synchronous or awaitable. The controller
calls them at its existing transport barriers. Each producer owns its state and
must cancel its tasks in `close`; every producer gets cleanup even when another
producer fails. Recovery prefixes may be declared as `recoverable_context_prefixes`.
The memory and registry plugins are independent examples of this seam.
A producer may declare `provider_name` to run only when that provider is selected
in the voice projection's `providers` list. Selection combines the agent binding,
realtime channel and application whitelist. Installed producers with no selected
provider receive no delivery hooks; all producers still receive cleanup.

A plugin is a complete vertical application feature, rather than a tool palette.
Installing or removing one changes startup composition and requires a server
restart; changing its agent binding leaves its installed services and data intact.
These changes are expected to be infrequent. While voice is active, a binding
change closes the ephemeral call and clears pending context and producer tasks.
The canonical worker owns transcription commits independently of the realtime
binding: a final accepted transcript still persists if voice is disabled before
its queued execution. It applies the binding and drains final voice turns before the
server publishes `realtime_reconnect_requested` with the owning browser's client
`call_id`. The starter and Harness renegotiate from a fresh canonical snapshot.
Reconciliation runs outside the binding operation, so an agent tool can request
its own binding change without waiting for itself to finish.

A manual stop cancels the client's intent to reconnect. Stale or foreign call IDs
are ignored, and disabling the realtime binding ends voice without restarting it.
Failed reconciliation reports an error rather than opening a call from uncertain
history. Server shutdown cancels pending reconnect tasks. This is a full call
replacement; no claim is made that an existing model can unsee provider context.
The reconnect signal is transient control data, never a conversation item.


Keep this seam narrow: it supplies context, not another event bus or task runtime.
Applications do not need a new controller subclass just to combine these plugins.
