# Application assembly and ownership

`Application` is both an immutable declaration and the owner of one running
server. Declare it in an importable module; workers import that declaration to
construct agents, not to share the server's Python objects.

## Declare, compile, build, run

These are four different operations:

| Operation | Effect |
| --- | --- |
| `Application(...)` | Validate declaration fields. No service construction, worker, listener or browser. |
| `application.compile()` | Produce an `ApplicationPlan`: resolved agent recipes, plugins, capabilities, static server declarations and surfaces. No `configure` hooks or service factories. |
| `application.build(...)` | Run plugin declaration builders, resolve factories and dependency graph, construct services and register ASGI routes. No service startup or inference. |
| ASGI lifespan | Reserve `Application.current()`, initialize surfaces, start services and sessions; close sessions and stop services on exit. |

Keep live acquisition in `start()`, not imports or factories. Factories execute
before lifespan; an error later in build is not a general resource transaction.

This service-only example verifies the boundary without a model or listening
socket. `TestClient` enters and exits the real ASGI lifespan:

```python
from fastapi.testclient import TestClient
from the_framework import Application, BuildContext, ServerExtension

transitions = []

class Catalog:
    def __init__(self, context):
        self.owner = context.application
        transitions.append("constructed")

    def start(self):
        assert Application.current() is self.owner
        transitions.append("started")

    def stop(self):
        assert Application.current() is self.owner
        transitions.append("stopped")

application = Application(
    name="Catalog service", version="1", surfaces=(),
    server_extensions=ServerExtension(name="catalog", service_factory=Catalog),
)
plan = application.compile()
assert not transitions
assert plan.application is application
app = application.build(context=BuildContext(region="eu"))
assert app.state.application is application
assert transitions == ["constructed"]
assert "catalog" not in application.context.services
with TestClient(app):
    assert Application.current() is application
    assert application.service("catalog").owner is application
    assert application.context.services["catalog"] is application.service("catalog")
assert transitions == ["constructed", "started", "stopped"]
assert "catalog" not in application.context.services
try:
    Application.current()
except RuntimeError:
    pass
else:
    raise AssertionError("application reservation leaked beyond lifespan")
```

Only one Application may run in a process, including across server threads. A
second lifespan is rejected without replacing the first. Reservation is released
on normal shutdown, startup failure and cancellation. Rebuilding the running
Application is rejected; create a new composition and use the host's supervised
replacement path when changing installed services.

## Declaration fields

| Field | Meaning |
| --- | --- |
| `name`, `version` | Required nonblank application identity. |
| `agents` | Public `AgentSpec` catalogue; empty means service-only. |
| `plugins` | Complete `Plugin` features, or named agent-extension classes as shorthand. |
| `server_extensions` | Application infrastructure; plugin-owned components belong in their Plugin. |
| `surfaces` | Explicit client declarations. For an agent app, `None` selects `ui/` beside its referenced module (must exist); `()` disables UI. |
| `security` | Declared HTTP policy; `build(security=...)` overrides it. |
| `title`, `description`, `docs_url` | ASGI/OpenAPI metadata; docs URL defaults to `/docs`. |
| `validate_responses` | Validate ordinary endpoint JSON results; defaults to true. |
| `max_upload_bytes` | Positive multipart upload ceiling, or `None`; not a universal process/disk quota. |

Sequences accept a single declaration, list or tuple. Configuration fields are
frozen; reconstruct a declaration for a coordinated change. Resource attributes
such as `services`, `context`, `app` and `default_session` are runtime metadata,
not serialized configuration or worker payloads.

## Build an agent application

Use the runnable [starter path](../guides/first-application.md), which supplies
security and desktop ownership. An application with declared agents must provide:

- `reference`: a `module:application` import reference available to its workers;
- a fresh `BuildContext(data_root=...)` (derives `session_path` and `surface_root`);
- an explicit HTTP security policy;
- authenticated `handshake`, or both `event_handshake` and
  `application_handshake`, for the two socket channels.

Missing inputs are errors, not a fallback to service-only mode. `build()` returns
a FastAPI ASGI adapter, not a listening server. `LocalServer.serve()` or an ASGI
host enters its lifespan; see [hosting](hosting.md).

Advanced build parameters are `agent`, `supervisor`, `session_factory`,
`session_projection`, `health_projection` and `session_extensions`. These change
session assembly; see [hosting](hosting.md#customizing-session-assembly). They
must not reuse a live controller across sessions.

## Resource scopes and lookup

| Owner | Resource | Lookup while running |
| --- | --- | --- |
| Application | Shared services, surfaces, session controllers | `application.service(name)` |
| SessionRuntime | One worker/fleet/bridge, scoped services and bindings | `runtime.service(name)` |
| Worker Agent | Canonical `Session`, `Context`, tools and inference | `agent.session`, `agent.context`, `agent.bridge` |
| BuildContext | Fixed inputs/options/contributions and live read-only service view | `context.require(key)`, `context.plugin(name)`, `context.services[name]` |

A session borrows application services but never a sibling's session services.
Application services cannot depend on session services. An application service
lookup therefore **cannot resolve `runtime`**: that service belongs to a session.
Inject `runtime` into a session-scoped factory, use the controller already in
scope, or explicitly choose `application.default_session` when that is truly the
intended conversation. There is no global current-session pointer.

On a session route, `request.app.state.session` identifies that controller;
`request.app.state.application` is always the global Application. The root
adapter has no session controller. `EndpointContext.application` is also the
global Application, not an implicit default session.

`application.extensions` is a read-only map of resolved server declarations;
`application.plan` is the assembly plan; `application.access_policy` is the
effective security policy. `context.services` exposes services only after
successful startup and removes them during shutdown. Constructor injection uses
constructed instances earlier; do not confuse these two phases.

## Independent conversations

`await application.open_session(...)`, `application.list_sessions()` and
`await application.close_session(id)` manage independent controllers, not cloned
Applications. A public recipe can run in several sessions. Each has its own
worker, canonical history/configuration/state, storage path and plugin bindings.
Private specialists have their own sessions and never write the caller's history.

`default_session` references the open default controller, or `None` after closure;
`default_session_id` retains its identity. Root conversation routes delegate to
that session. `/sessions/{id}/...` addresses an explicit one, including the
default. Closing the default does not close siblings or shared services; root
conversation aliases are unavailable until it reopens. Server shutdown closes
sessions before root services, in reverse dependency order.

See [sessions](sessions.md) for persistence and session management,
[plugins](plugins.md) for declaration resolution, and [services](services.md)
for construction, lifecycle and HTTP contracts.

Sources: `server/composition/application.py`, `declarations.py`, `services.py`;
`server/runtime/session.py`.
