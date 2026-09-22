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
- `agent.extensions`: plugin contributions, tools, providers, hooks, endpoint
  declarations and specialist contracts;
- `agent.runtime`: execution loops, worker protocol and process entrypoint;
- `agent.spec`: the public agent construction declaration;
- `server.composition`: application declarations, dependency validation and
  plugin/service installation;
- `server.api`: HTTP/WebSocket exposure, authorization and endpoint adaptation;
- `server.runtime`: application/worker supervision, bridges, discovery and live
  voice orchestration;
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

Reorganization verification: 875 application/agentic Python tests pass, including
real worker subprocesses and wheel isolation. All 90 framework modules import
without the product package. The public facade export sets remain unchanged
(14 root, 138 agent and 22 server symbols); architecture checks protect model
independence and the the_framework/product boundary. Ruff's import/name/error checks and
`git diff --check` pass. This is a source/package verification, not a production
restart or a new device acceptance run.

This repository distributes the `the_framework` import package as `b4pt0r-the-framework` and bundles the neutral `starter`
template, without the product application. Run `the-framework bootstrap` (or
`python -m the_framework bootstrap`) to copy the editable starter into a code
directory while keeping its private data in a separate directory. The package
is published on PyPI as `b4pt0r-the-framework`. There are no legacy top-level `agent`, `server`
or `agent_plugins` import aliases. The wheel isolation test extracts only the
`the_framework` package, blocks product imports, and constructs the general
plugins using their packaged Markdown resources.

`AgentApplication` is the single root declaration. It owns exactly one durable
primary `AgentSpec`, the plugin specifications, server extensions, security and
client surfaces. Calling `compile()` produces an immutable, process-neutral
plan without invoking server runtime factories. This lets a worker reconstruct
its agent from the same declaration without server-only inputs. `build(context)`
resolves those factories and validates the complete server graph before any
service, route or mount is started.

Application code supplies identity, plugins, domain services, policies and UI
surfaces. The core owns process supervision, ordered commands, lifecycle,
validation, error translation and transport plumbing.

## The small path

A complete HTTP service can be assembled without manipulating FastAPI or a
lifecycle callback:

```python
from the_framework import (
    AgentApplication,
    AgentSpec,
    Extension,
    Plugin,
    SessionPolicy,
    endpoint,
)


@endpoint(
    "get",
    "/api/v1/greeting",
    request={"type": "object", "properties": {}},
    response={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    authenticated=False,
)
def greeting():
    return {"message": "hello"}


application = AgentApplication(
    name="Example",
    version="1.0",
    primary_agent=AgentSpec(
        name="example",
        description="Example primary agent.",
        session=SessionPolicy.durable(),
    ),
    plugins=(Plugin(name="greeting", runtime=Extension(
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
`AgentApplication({**application, "plugins": plugins, "extensions": extensions})`:
validation then sees the final state, not intermediate per-key assignments.

## Adding state and lifecycle

### Model-based endpoint contracts

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

### Service lifecycle

Use constructor callbacks for permanent service relationships and the existing
`Extension.start`/`stop` adapters for lifecycle-specific integration. A callback
such as a restart barrier is not itself a new service or manager. Keep an absent
capability absent: wrapping a missing restart callback would falsely advertise
restart support and fail only when invoked.

An ordinary service only needs conventional `start()` and `stop()` methods:

```python
library = MediaLibrary(...)

media = Extension(
    name="media",
    service=library,
    endpoints=(browse_media, stream_media),
    requires=("runtime",),
)
```

Services start in dependency order and stop in exact reverse order. A partial
startup is rolled back. Optional services can set `critical=False`; their
failure remains visible in the graph health report without taking down the
application. Explicit `start`, `stop` and `health` adapters exist for advanced
or third-party services, but are not required by the common path.

When construction itself depends on other declared services, use a synchronous
factory. Its keyword arguments are exactly the service-bearing names listed in
`requires`; construction still happens before startup, while the live
`ServiceContext` receives each instance only after its successful start:

```python
realtime = Extension(
    name="realtime",
    service_factory=lambda runtime: RealtimeController(runtime),
    requires=("runtime",),
)
```

The same dependency injection applies to an `Extension` returned by a plugin's
server runtime factory. A plugin requiring another plugin's service must also
declare the corresponding versioned public `CapabilityRequirement`; the
capability dependency determines startup order. A dependent cannot be declared
without its provider. Installed plugin runtimes are fixed for the server
lifetime; removing a provider requires updating the startup declaration and
restarting the server.

Runtime lookup goes through the single application context:

```python
runtime = app.state.application.service("runtime")
```

Domain code should not create additional untyped `app.state` attributes.

## Pairing the worker and server sides of a plugin

For an agent-only plugin, pass its class directly:

```python
application = AgentApplication(
    name="Example", version="1", primary_agent=primary,
    plugins=(MemoryPlugin, BashPlugin),
)
```

The class must declare its `name`. The application normalizes it into a
`Plugin` without constructing or activating the plugin. Duplicate identities,
private-agent discovery and lifecycle validation are identical to the full form.
Use the full form when adding server runtime, dependencies or activation policy;
there is no second installation path.

`Plugin` records that a product capability has an agent binding, a runtime,
private specialist agents, or any combination of those:

```python
images = Plugin(
    name="images",
    agent=ImageGenerationPlugin,
    runtime=Extension(
        name="image_api",
        service=image_service,
        endpoints=(image_service,),
        requires=("runtime",),
    ),
)
```

A feature can own several server components without splitting its runtime into
unrelated application extensions. Its `runtime` may be a tuple of `Extension`
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
`AgentApplication.extensions` until the high-level plugin API absorbs their
ownership cleanly.

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
binding. Application and plugin endpoints, WebSockets, middleware and mounts
are installed on one server at startup; route and mount collisions fail before
any service starts. OpenAPI includes the complete fixed endpoint set. Required
bindings, such as the system boundary, cannot be disabled.

## Agent construction

```python
from the_framework.agent import AgentResources, AgentSpec

agent_spec = AgentSpec(
    name="companion",
    description="Owner of the canonical conversation.",
    plugins=(memory, images, tools),
    instructions=(identity_instruction,),
    projections={"mobile": mobile_projection},
    command_middleware=(measure_turns,),
    session=SessionPolicy.durable(),
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
by default and can opt into `SessionPolicy.resident()` or
`SessionPolicy.durable()`. `QueuePolicy` controls bounded FIFO/latest delivery
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
invalid policy graphs fail at construction. The Harness supplies its own policy
in application assembly, including desktop and Quest permissions.

Authenticated endpoints require an explicit application `SecurityPolicy`.
There is no implicit allow-all policy in `AgentApplication`. The registry:

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
through `Extension(websockets=(...))`. They share the same authentication and
authorization policy, reject unauthorized upgrades with close code `1008`, and
are collision-checked before any route is installed.

Protocols that authenticate during their own handshake can construct a
`WebSocketEndpoint(..., handshake=...)`. The handshake returns a small
connection context that is injected into the handler, or `None` to reject the
upgrade. This supports versioned subprotocols, replay cursors and private-client
credentials without weakening the ordinary HTTP security policy.
Server-push event handlers must also read the peer's disconnect signal while
waiting for the next event. The framework's local and remote event streams use
one shared routine for this, so an idle disconnected client releases its
subscription promptly instead of lingering until a heartbeat or shutdown.

## Built-in application primitives

Reusable agent plugins live in `the_framework.plugins`, separate from the product's
`the_harness.plugins`: `bash`, `system`, `registry`, `scheduler`, and
`web_search`. Import each capability directly from its subpackage; importing
one does not load the other plugins or product services. Their instruction
files ship alongside their implementations. Application-specific defaults
(including the shell working directory) belong in application configuration.

`the_framework.plugins.memory` owns storage, retrieval, curation tools and orchestration;
applications specialize `memory.plugin.MemoryPlugin.curator_instructions`.
`the_framework.plugins.browser` accepts an explicit runtime directory and uses
`the_framework.server.clients.browser`/`the_framework.server.clients.browser_rpc` for the persistent Playwright process.
`the_framework.plugins.realtime` provides canonical voice projection with application-owned
provider selection. `the_framework.server.runtime.realtime.RealtimeController` owns transport and
delegation; subclasses contribute context through `start_context`, `ready_context`,
`flush_context` and `close_context`. The latter must stop and await their producers.
Pandora's thin plugin specializations retain her prompts and defaults; her live
controller owns video/pleasure context. No product import is required by these
shared mechanisms. Runtime observers use `register_observer`/`unregister_observer`.

An `ApplicationRuntime(WorkerSupervisor(session_path))` can be installed as an
`Extension` service, with `CanonicalAgentApi` endpoints in a dependent extension.
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
  capability bridge;
- `AgentApplication`, `Extension`, `Plugin`, `WebSocketEndpoint` and
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

Middleware and mounted ASGI applications belong on an `Extension`, so they are
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

## The Harness as the reference application

The product declaration lives in the separate sibling The Harness checkout at
`../the_harness/the_harness/application.py`. It currently
declares Pandora, fourteen plugins, nine private specialists and the main client
surface. Server-only process inputs are added in
`../the_harness/the_harness/app.py`, whose job is deliberately limited
to orchestration:

1. build the canonical runtime;
2. build paired-client services;
3. build media storage, generation, observation and indexing;
4. build the HTTP, WebSocket and Chromium-facing API;
5. compose dependency-grouped extensions;
6. call `build_application()` with one explicit `BuildContext`.

The implementation for those steps is split by ownership across
`runtime_composition.py`, `client_composition.py`, `media_composition.py`,
`api_composition.py`, `remote_composition.py` and
`extension_composition.py`. Product policy remains in these modules; no product
composition is imported back into `the_framework`.

## Design rule

The framework follows progressive disclosure:

- **simple:** `AgentApplication`, `Extension`, `Plugin`, conventional
  service lifecycle;
- **composable:** capabilities, dependencies, decorated endpoint objects,
  middleware, mounts, named projections and plugin specifications;
- **advanced:** explicit lifecycle adapters, authorization policies and custom
  response objects.

Adding power must not make the small path more ceremonial. An application
feature belongs in the core only when it is a reusable orchestration primitive;
identity, prompting, device semantics, media policy and product UX remain in
the application's extensions.
