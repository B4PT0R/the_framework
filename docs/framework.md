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

`ApplicationSpec` is the single root declaration. It owns exactly one durable
primary `AgentSpec`, the plugin specifications, server extensions, security and
client surfaces. Calling `compile()` produces an immutable, process-neutral
plan without invoking server runtime factories. This lets a worker reconstruct
its agent from the same declaration without server-only inputs. `build(context)`
resolves those factories and validates the complete server graph before any
service, route or mount is started.

`app.state.application.plan` keeps that compiled recipe. The resolved server
declarations live in `app.state.application.server_extensions`, an immutable
mapping in construction order. Use `app.state.application.service(name)` for a
successfully started service. Building the same declaration again creates a
separate runtime without rewriting the recipe.

Application code supplies identity, plugins, domain services, policies and UI
surfaces. The core owns process supervision, ordered commands, lifecycle,
validation, error translation and transport plumbing.

## The small path

A complete HTTP service can be assembled without manipulating FastAPI or a
lifecycle callback:

```python
from the_framework import (
    ApplicationSpec,
    AgentSpec,
    ServerExtension,
    Plugin,
    endpoint,
)


@endpoint("get", "/api/v1/greeting", authenticated=False)
def greeting() -> dict[str, str]:
    return {"message": "hello"}


application = ApplicationSpec(
    name="Example",
    version="1.0",
    primary_agent=AgentSpec(
        name="example",
        description="Example primary agent.",
        session_mode='durable',
    ),
    plugins=(Plugin(name="greeting", server_extension=ServerExtension(
        name="greeting_api", endpoints=(greeting,),
    )),),
)

app = application.build()
```

The declaration is immutable. `build_application` validates the complete
graph before installing routes: duplicate names and paths, missing or cyclic
dependencies, malformed JSON schemas, missing security policy and duplicate
mounts fail immediately with a precise composition error.

Structured declarations and runtime records use `modict`. Construct them with
named fields or a mapping; runtime declarations can contain non-JSON references
without converting or copying those objects. Browser execution handles live in
attributes outside its configuration mapping. For a coordinated change, rebuild
the complete declaration, for example
`ApplicationSpec({**application, "plugins": plugins, "server_extensions": server_extensions})`:
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

Installing one `ApplicationRuntime` service through a `ServerExtension` automatically
connects the plugin host's binding updates and startup snapshot to its worker,
and registers the agent-facing plugin and installed surface controls. Supply
`ui_root` in `BuildContext` to bootstrap a surface's initial artifact. There is
no separate post-build control-registration call.

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
session writes, and confirms application before the server persists its binding.

### Standard conversation assembly

`ApplicationSpec.build_conversation(reference, context, ...)` assembles the
supervised primary worker, runtime, agent HTTP API, health endpoint and both
WebSocket transports. The reference is the importable `module:application`
resolved by workers. Authentication stays explicit:

```python
from the_framework import BuildContext

app = application.build_conversation(
    "my_app.application:application",
    BuildContext(data_root=data_dir),
    security=security,
    handshake=security.handshake,
)
```

The default runtime derives `session.json`, `plugins.json` and `surfaces/` from
`data_root`. `handshake` applies to both sockets; `event_handshake` and
`application_handshake` independently override it when their wire protocols differ.
The declared primary agent name is passed to the supervised worker automatically.
The framework owns construction, ordering and cleanup; it does not start inference
or perform network authentication during declaration/build.

Advanced applications can pass `runtime_factory(supervisor)` to specialize the
runtime, or inject a supervisor for another worker executable. An injected
supervisor must refer to the same session and, when declared, application reference.
`session_projection` selects history presentation, `health_projection(payload)`
selects diagnostics, and `reset_handler(runtime)` owns selective reset policy.
`extensions(runtime)` returns application-owned server declarations, such as reset
endpoints and access middleware. The starter and Harness use this same path.

Set the fixed `restart` input on `BuildContext` for the default runtime's supervised
restart callback. Runtime subclasses may specialize restart and UI notifications
through `restart_server`, `surface_notify` and `surface_progress`. Managed surfaces
use those methods automatically; there is no need to copy publish callbacks into
construction inputs.

`ApplicationSpec.build(context)` remains the low-level server composition operation
for applications that explicitly assemble their own services or do not need a
conversation runtime. The former public `application_server_extensions` helper
has been removed; ordinary conversation applications should migrate to
`build_conversation` rather than recreate its runtime/API/transport wiring.

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
Connect it through `ApplicationRuntime.reset_transaction_factory`; the application
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
acquired by its own unsuccessful `start()`. Optional services can set `critical=False`; their
failure remains visible in the graph health report without taking down the
application. Explicit `start`, `stop` and `health` adapters exist for advanced
or third-party services, but are not required by the common path.
An `@endpoint` method named `start` or `stop` is an HTTP handler, not a
lifecycle hook; provide an explicit adapter if that service also needs startup
or shutdown behavior.

A service that depends on the canonical worker may also need to pause while
that worker's persisted session is reset. Register a `suspend`/`resume` pair on
`ApplicationRuntime.register_reset_hooks()` when constructing that service.
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
dependency. Application settings belong in the enclosing plugin factory or a
closure, leaving the service factory's parameters for service inputs.

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

If a service has `@endpoint` methods, set `endpoints="service"` to discover
them on the constructed instance, including one returned by `service_factory`.
Omitting `endpoints` exposes no routes; use an explicit tuple when routes come
from another object or you intend to expose only a subset. Route collisions
and invalid schemas are still checked before startup.

For a service that constructs its own WebSocket declarations, implement
`websocket_declarations()` returning its `WebSocketEndpoint` values and set
`websockets="service"`. This works with `service_factory` as well, so the socket
handlers can refer to the constructed service without a separate pre-build step.

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
| `Plugin.server_extension` | Server declaration, service, or build-context factory returning one or more components; an import reference defers server imports |
| `Plugin.specialists` | Private `AgentSpec` declarations |
| `Plugin.provides_capabilities` | Versioned contracts exported by this feature |
| `Plugin.requires_capabilities` / `optional_capabilities` | Required or optional versioned contracts from other plugins |
| `Plugin.agent_binding_enabled` / `agent_binding_required` | Initial and mandatory binding to the primary agent; server installation remains fixed |
| `ServerExtension.requires_services` / `optional_services` | Names of local services supplied to its factory, in dependency order |
| `ApplicationSpec.server_extensions` | Application-wide server infrastructure |
| `AgentSpec.extensions` | Contributions installed in that agent |
| `AgentSpec.specialist_profiles` | Compiled worker profiles; application code normally declares specialists on their owning `Plugin` |

The normal import is the complete plugin, not its agent and server parts:

```python
from the_framework.plugins.bash import BASH
from the_framework.plugins.memory import MEMORY
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM

application = ApplicationSpec(
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
The browser defaults to the agent session directory, where a browser RPC host
must be running; applications with another browser host specialize its binding.
`BrowserService.open_application_ui(url)` opens the application's chosen route;
`application_ui_origins` permits explicitly trusted HTTPS origins in addition to
loopback. Query and fragment values are excluded from Chromium launch arguments
and passed through navigation. The isolated UI target/profile `harness_ui` and
its historical RPC command remain stable for saved sessions and browser processes
that survive server or worker upgrades.

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

`Plugin` records that a product capability has an agent binding, a runtime,
private specialist agents, or any combination of those. For a feature with
one self-contained server service, pass the service directly. Its decorated
endpoints and WebSocket declarations are discovered, and its conventional
`start()` and `stop()` methods participate in application lifecycle:

```python
images = Plugin(name="images", agent_extension=ImageGenerationExtension, server_extension=image_service)
```

`Plugin.name` is the shared feature identity. Its agent extension is bound under
that name for configuration, state, tool namespaces, specialists and live
binding controls, without changing the extension class. An `AgentExtension`
used directly by `AgentSpec.extensions` keeps its own name (or class name).
This lets a complete plugin supply the identity while its implementation stays
reusable. An already loaded extension cannot be renamed.

The service may instead be returned by a server-side factory that receives
`BuildContext`. Use `ServerExtension` when that service needs explicit dependencies,
route selection, middleware, mounts or lifecycle adapters, or when the plugin
owns several server components:

```python
images = Plugin(
    name="images",
    agent_extension=ImageGenerationExtension,
    server_extension=ServerExtension(
        name="image_api",
        service=image_service,
        endpoints=(image_service,),
        requires_services=("runtime",),
    ),
)
```

A feature can own several server components without splitting its runtime into
unrelated application extensions. Its `server_extension` may be a tuple of `ServerExtension`
objects, or a server-side factory receiving `BuildContext` and returning that
tuple. Application and plugin services share one dependency graph: components
are constructed and started in dependency order, then stopped in reverse order.
This also lets an application extension depend on a plugin-owned service.
The worker's `compile()` path never
calls that factory. Use an importable `"package.module:factory"` reference when
the worker should not even import the server module; the reference is resolved
only by `build(context)`. For example, a media plugin can own a store, an index and
an HTTP API, while the application supplies only process-local objects through
`BuildContext` at server build time. Shared core services still live in
`ApplicationSpec.server_extensions`: for example, the canonical worker supervisor
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
factory's named parameters). Use those injected objects during construction.
Use `context.services` in deferred callbacks that need a service during operation,
including authentication. It does not expose partially constructed services.
`context.provide` has been removed: services no longer overwrite or extend the
construction inputs. Create a fresh context for each application build.

Some integrations are contributions to an owner's policy rather than a service
dependency. A runtime factory can call `context.contribute("feature.policy",
contribution)` whether it runs before or after the owner factory. The owner
reads `context.contributions("feature.policy")` in its service factory, after
all plugin runtime factories have declared their contributions. Contributions are
sealed before service construction: contributing from a service factory or after
build raises an error. Contribution names and values are contracts defined by the owning feature; the framework
transports them without interpreting them. These build-time values are
process-local, not agent-session state or a hot runtime registry.

In a larger codebase, keep the `Plugin` declaration in the feature's package
alongside its agent class and server runtime factory. Export that object and
list it in `ApplicationSpec.plugins`; the application assembly then chooses
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

When launching the generic primary worker, pass `--plugin-state` the same
`plugins.json` path supplied under `plugin_state_path` in the server's
`BuildContext`.
The worker applies that persisted binding state before constructing its agent,
including after a crash or session reset. If the file does not yet exist, the
server imports the worker's existing session bindings on first startup; after
that, the server's plugin state is authoritative. Private specialist workers
do not receive this argument. The starter demonstrates the complete wiring.

## Agent construction

`AgentSpec` declares what an agent is; `AgentResources` supplies the process-local
inputs needed to construct it: inference client, loaded configuration/state and
callbacks that save their changes. It is not another plugin or application
composition layer. Ordinary applications provide a `resources(session_path)`
factory so the generic worker owns construction and the application chooses its
storage. Use explicit resources when constructing an agent directly.

```python
from the_framework.agent import AgentResources, AgentSpec

agent_spec = AgentSpec(
    name="companion",
    description="Owner of the canonical conversation.",
    extensions=(memory, images, tools),
    instructions=(identity_instruction,),
    projections={"mobile": mobile_projection},
    command_middleware=(measure_turns,),
    session_mode='durable',
)

agent = agent_spec.build_agent(
    session_path,
    resources=AgentResources(
        configuration=config_store.load(),
        persist_config=config_store.save,
    ),
)
worker = agent_spec.build_worker(agent, receive, send)
```

The same `AgentSpec` declares a private specialist. Specialists are ephemeral
by default and can opt into `session_mode="resident"` or
`session_mode="durable"`. `QueuePolicy` controls bounded FIFO/latest delivery
and durable task journaling. A plugin owns its specialists and calls them
through `agents.call()`, `agents.submit()` and `agents.publish()`; another
plugin can reach their capability only through an explicit versioned public
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
selection. Agent construction overlays explicit resources on declared
configuration, without retaining a competing source profile.

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
original namespaced spec. Imported role text becomes an ordinary spec instruction;
exporting uses the current instructions rather than the old profile text.

Terminal private-agent results enter a durable outbox only after their terminal
state is persisted. Delivery and acknowledgement are idempotent, so a server or
worker crash can replay the result without losing it or applying it twice.

Middleware has the small async-generator contract
`middleware(agent, command, call_next)`. It adds cross-cutting behavior such as
timing or tracing without subclassing the worker. Session projections are pure
named functions owned by the application, so the generic worker does not know
about desktop, mobile, remote or product-specific timeline semantics.

## Optional live context

The voice controller owns transport, delegation barriers and ephemeral-context
reconciliation, not knowledge of memory or registry contents. Applications compose
`MemoryLiveContext` and `RegistryLiveContext` from their respective general plugins
when wanted, through `start_context`, `worker_context`, `flush_context` and
`close_context`. Context producers own their caches, revision tracking and tasks;
the controller owns the ordered, token-bounded delivery path. Close producers
before releasing the transport. The Harness demonstrates this composition beside
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
There is no implicit allow-all policy in `ApplicationSpec`. The registry:

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

## Built-in application primitives

Reusable agent plugins live in `the_framework.plugins`, separate from the product's
`the_harness.plugins`: `bash`, `system`, `registry`, `scheduler`, and
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
delegation; subclasses contribute context through `start_context`, `ready_context`,
`flush_context` and `close_context`. The latter must stop and await their producers.
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
Pandora's thin plugin specializations retain her prompts and defaults; her live
controller owns video/pleasure context. No product import is required by these
shared mechanisms. Runtime observers use `register_observer`/`unregister_observer`.

An `ApplicationRuntime(WorkerSupervisor(session_path))` can be installed as an
`ServerExtension` service, with `CanonicalAgentApi` endpoints in a dependent extension.
For a declared application, pass `application_reference="my_app:application"`
to `WorkerSupervisor`, with optional `agent_name` and `plugin_state_path`.
The default fleet uses the same application reference to build private agents;
no custom worker command or forwarding fleet factory is needed. A custom
`command` remains available for other worker executables, and cannot be combined
with these declaration inputs; custom fleet factories remain explicit overrides.
The default generic worker persists its canonical session. Durable configuration
and plugin-state storage are separate application choices. Declare a
`resources(session_path) -> AgentResources` factory on `AgentSpec`, returning
`configuration`, `state`, their `persist_config`/`persist_state` callbacks and an
optional inference `client`. It runs once when constructing the agent, not while
compiling its declaration. Tests or embedding callers may instead pass a complete
`AgentResources` to `build_agent(resources=...)`; that bypasses the factory, with
no implicit merge or persistence side effects. The default `the_framework.agent.runtime.worker_process` does
not persist configuration updates across process recreation.

`the_framework.utils.persistence.MappingStore(path, field="settings")` supplies private atomic
JSON snapshot persistence for these callbacks. Use `store.load()` as the initial
configuration and `store.save` as its persistence callback. The document contains
`version` and the selected mapping field. Application-specific migrations remain
outside this store. Snapshot replacement is atomic; coordinating read/modify/write
operations remains the application's single-writer responsibility.

The public `the_framework.server` package exports reusable pieces for common agent apps:

- `CanonicalAgentApi` for status, session pages, configuration, compaction,
  interruption and application-defined atomic reset;
- `ApplicationHealthApi` for aggregate and per-service lifecycle health;
- `CanonicalTransportSockets` for ordered agent events and the application
  capability bridge. `build_conversation()` exposes it as the
  `canonical_transport` service. Its `event_connection_count` counts accepted
  canonical event sockets, separately from internal stream consumers, the
  application bridge and paired remote connections. Connectivity does not imply
  window visibility or user attention; product presence policy belongs to the app;
- `ApplicationSpec`, `ServerExtension`, `Plugin`, `WebSocketEndpoint` and
  `build_application` for composition.

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

The surface service copies sources into an isolated workspace, executes the
fixed argv without a shell, verifies the artifact, hashes it and creates an
immutable release. Candidates are served on authenticated preview URLs.
Publication atomically swaps the persisted active release and notifies clients;
rollback swaps back without rebuilding. Build success is evidence only: the
agent remains responsible for inspecting the preview before publication.
`server_routes` explicitly reserves routes beneath a surface mount for an
application or plugin endpoint, such as a login/bootstrap exchange. Other
endpoints and WebSockets under the mounted path are rejected at build time.

Middleware and mounted ASGI applications belong on a `ServerExtension`, so they are
validated and installed with the rest of the graph, whether that extension is
declared directly by the application or owned by a plugin. Mounted ASGI apps
handle their own request authentication; the framework's `@endpoint` security
policy does not automatically wrap arbitrary mounts. Product code should not
add routes, middleware or mounts after `build_application()`.

## Standalone example

[`examples/minimal_agent_app.py`](../examples/minimal_agent_app.py) is a second,
independent application that imports only `the_framework`. It demonstrates an
authenticated endpoint, a lifecycle-managed service and graph-derived health
without importing any The Harness product module:

```bash
uvicorn examples.minimal_agent_app:app
```

## The Harness as a larger application example

The separate The Harness application uses the same `ApplicationSpec` and
`Plugin` API as the starter. Its declaration supplies one primary agent,
product plugins, their versioned dependencies and a multi-device client
surface. The server entrypoint supplies process-local inputs through
`BuildContext` and calls `build_conversation`, which owns the canonical runtime
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

- **simple:** `ApplicationSpec`, `ServerExtension`, `Plugin`, conventional
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

Import `CHAT` from `the_framework.plugins.chat` to install
`POST /api/v1/agent/prompt` and `POST /api/v1/agent/attachments`.
Both accept a client-generated command `id`; attachments use multipart form data
with `files` and an optional `prompt`. The plugin coordinates with the installed
Realtime API's lock, so attachment acceptance and voice startup cannot race.
`BuildContext.data_root` owns storage; the `chat.attachments_root` plugin option
selects a separate attachment directory when needed. No voice plugin is required for text-only apps.

`CHAT` exports `conversation.ingress` version 1. Other client transports declare
that capability requirement and inject the `chat` service into their factory,
then reuse `chat.ingress.submit(PromptRequest(...))`. Do not inspect voice state
and submit directly: selection and acceptance must hold the same lock as voice
startup, shutdown and binding changes. The operation returns `{"id": ..., "route":
"text" | "realtime"}`; in voice mode the id is the voice turn id. HTTP contracts,
authorization and per-client idempotent receipts remain owned by the caller.
The local prompt endpoint still returns its client-generated id.

`ChatIngress` is also available from `the_framework.plugins.chat.ingress` for
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

Keep this seam narrow: it supplies context, not another event bus or task runtime.
Applications do not need a new controller subclass just to combine these plugins.
