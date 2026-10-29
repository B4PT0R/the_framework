# Application and lifetime

[Documentation](../README.md) / [Reference](README.md)

Use this when you need exact ownership, construction and session-lifecycle rules. For the mental model first, read [Architecture](../explanation/architecture.md).

## Responsibility map

Modules are grouped by responsibility, not by implementation size:

- `agent.models`: payloads, configuration, events and state records;
- `agent.context`: canonical history, context construction, projections and
  compaction;
- `agent.extensions`: agent contributions, tools, providers, hooks and
  specialist contracts;
- `agent.runtime`: execution loops, worker protocol and process entrypoint;
- `agent.spec`: the public agent construction declaration;
- `server.composition.declarations`: `BuildContext`, `Plugin`, `ServerExtension`
  and versioned capabilities — construction inputs and feature vocabulary;
- `server.composition.application`: `Application`, compilation and server/session
  assembly; `composition.services` owns service lookup and lifecycle;
- `server.api`: HTTP/WebSocket declarations, discovery, authorization and adaptation;
- `server.runtime`: application/worker supervision, bridges and discovery;
- `plugins.realtime`: voice projection, transport and delegation orchestration;
- `server.clients`: browser shells, client surfaces, remote pairing and Tailscale;
- `deployment`: immutable images, validation, release selection and host supervision;
- `execution`: explicit local/host process launch, transport and control;
- `utils`: small primitives shared by agents, plugins and the server.

`utils.ids`, `utils.tokens` and `utils.persistence` are small shared framework primitives. They do
not belong to an agent execution loop or server owner. General plugins retain
their existing capability-oriented packages under `plugins/`.

`atomic_text_writer` commits at `os.replace`: serialization, file flush/fsync or
replacement errors preserve the previous snapshot and propagate, so owners can
roll back memory. After replacement, directory open/fsync/close failures are
logged at ERROR without raising: the new snapshot is committed and owners must
retain it, although crash durability is uncertain. This shared contract applies
to Session, MappingStore, registry, worker journals and application settings;
a failed directory sync must never cause a stale in-memory snapshot to overwrite
the committed file on the next mutation.

The `the_framework`, `the_framework.agent` and `the_framework.server` facades retain
the concise public construction API. Internal imports target the owning module directly; the
subpackages introduce no forwarding services or legacy module aliases.

The worker entrypoint is `python -m the_framework.agent.runtime.worker_process`.
Runtime prompt resources live next to that runtime and are verified from the
built wheel. Moving Python modules does not change persisted JSON discriminators
or the HTTP/WebSocket protocol.

This repository distributes the `the_framework` import package as `b4pt0r-the-framework` and bundles the editable `starter`
template, without the product application. Run `the-framework bootstrap` (or
`python -m the_framework bootstrap`) to copy the editable starter into a code
directory while keeping its private data in a separate directory. The package
is published on PyPI as `b4pt0r-the-framework`. There are no legacy top-level `agent`, `server`
or `agent_plugins` import aliases. The wheel isolation test extracts only the
`the_framework` package, blocks product imports, and constructs the general
plugins using their packaged Markdown resources.

`Application` is the application's global object. It carries both its declared
configuration and its server-owned resources: shared service graph, client
surfaces and independent session lifespans. The separate
ApplicationHost layer has been removed. Declare `application = Application(...)`
in an importable module and build its server through `application.build(...)`.
The ASGI server's `state.application` is that same object.

Constructing or compiling Application does not construct services, launch workers,
open a browser or start a server. Worker processes import and compile this module
without activating an application server. Python globals are process-local; the
server's live resources are not shared with those worker processes.

`build(...)` constructs the ASGI server and resolves service factories. With
declared agents it prepares a default session with authenticated text/attachment routes,
voice coordination when installed, and independent session hosting. Without an
agent it installs only declared services and surfaces. An agent application must
provide its importable reference, storage inputs and authentication; omission does
not silently build a service-only server. Building does not run Uvicorn or enter
the ASGI lifespan.

### Application and session lifetime

| Object | What it owns |
| --- | --- |
| `Application` | The unique running server's configuration, shared services, surfaces and session management. |
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
  Compaction defaults to `gpt-6-luna` with medium reasoning; customize it through
  the session's `compaction` configuration independently of the main agent model.
- UI history comes from the retained archive and named `SessionProjections`;
  Realtime owns its voice projection. They do not reuse Responses input as a
  display format. The item model `Compaction` and `CompactionResponse` payload
  remain distinct from runtime operations and keep their schemas.

Declaration fields such as `plugins` and `server_extensions` keep their input
shape. `application.extensions` is the read-only map of resolved server declarations.
`application.service(name)` reads only application-owned services. Every session,
including the default one, uses its own `session.service(name)` lookup with access
to shared resources. `application.default_session` points to the currently open
default controller after build and while open; it becomes `None` on closure.
`default_session_id` remembers its identity even while closed.
The application has no global agent-binding host: plugin_host belongs to each
session. Root HTTP/WebSocket aliases delegate to the default session; named routes
under `/sessions/{id}` work for every session, including that one.

`await application.open_session(...)`, `application.list_sessions()` and
`await application.close_session(id)` manage conversations. Creating one assembles
its controller and scoped ASGI routes, not another Application or a cloned root
definition. The default session can be closed and reopened through the same API. Closing it
leaves application services and sibling workers running; root conversation aliases
report unavailable until it is reopened. Resuming it retains its identity,
configuration and recipe. Shared application services are reused; session services, plugin
bindings and health remain scoped to that conversation. `session.application` and
`session.context.application` refer to the global Application. Each session
adapter exposes `state.application` as the global object and `state.session` as
its own controller; request handlers can select this explicit session through
`request.app.state.session`. The root adapter has no session controller of its
own. There is no global mutable current-session pointer.

`compile()` produces a validated `ApplicationPlan` without running factories.
Configuration fields remain in the mapping; live resources use metadata attributes
outside that mapping. Worker compilation therefore cannot capture running services
from the server's Python object. Runtime resources remain outside serialized worker payloads.

`service_factory=...` provides deferred construction and dependency injection.
Objects supplied directly as `service=...` are used as provided, without cloning.
Application-scoped services are shared across sessions; concurrent workers use
distinct session storage paths. Shutdown closes hosted sessions before stopping
root services in reverse dependency order. Application-scoped services cannot
depend on session-scoped services; session services may consume shared resources.
The desktop process owner binds/listens
and runs ASGI separately; its persistent browser survives server replacement.

`agent.bridge.call(...)` sends a capability request to its server-side
`session.bridge`. An HTTP handler's `EndpointContext.application` now refers to
the global Application itself. No transport or persistence migration is required.

Application code supplies identity, plugins, domain services, policies and UI
surfaces. The core owns process supervision, ordered commands, lifecycle,
validation, error translation and transport plumbing.

---

[Reference index](README.md) · [Documentation paths](../README.md)
