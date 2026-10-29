# Services, dependency injection and HTTP

Server services own resources shared by HTTP, workers through the application
bridge, or other services. Declare them through `ServerExtension`: the framework
constructs the dependency graph, installs declared transports and owns lifespan.
Do not register routes by mutating FastAPI during plugin startup.

For durable domain storage used by both tools and HTTP, read the
[Notebook implementation](../../examples/notebook/README.md). This page uses the
smaller, existing [counter service](../../examples/minimal_agent_app.py) to make
HTTP behavior easy to verify. Despite that file's historical name, it is
**service-only**, not a model-backed agent application. Its hardcoded bearer token
is an executable test credential, not a production authentication design; its
in-memory counter is neither durable nor a concurrency-safe database.

## A complete composition and request test

The example imports the counter's strict `Increment`/`CounterValue` models,
authenticated decorated method and security policy. A second component receives
the same counter by dependency injection and exposes a scoped read endpoint:

```python
from fastapi.testclient import TestClient
from the_framework import Application, ServerExtension, endpoint
from examples.minimal_agent_app import BearerSecurity, CounterService

lifecycle = []

class CounterReadApi:
    def __init__(self, context, counter):
        self.owner = context.application
        self.counter = counter

    def start(self):
        assert self.counter.running
        lifecycle.append("reader started")

    def stop(self):
        assert self.counter.running  # dependency stops after its consumer
        lifecycle.append("reader stopped")

    @endpoint("get", "/api/v1/counter", context="ctx",
              authorization={"scope": "counter:write"})
    def read(self, ctx):
        assert ctx.application is self.owner
        return {"value": self.counter.value, "reader": ctx.principal.id}

application = Application(
    name="Counter API", version="1", surfaces=(), security=BearerSecurity(),
    server_extensions=(
        ServerExtension(name="reader", service_factory=CounterReadApi),
        ServerExtension(name="counter", service_factory=CounterService),
    ),
)
app = application.build()
headers = {"Authorization": "Bearer example-secret"}
with TestClient(app) as client:
    assert application.service_graph.started == ["counter", "reader"]
    path = "/api/v1/counter/increment"
    assert client.post(path, json={"amount": 2}).status_code == 401
    assert client.post(path, headers=headers, json={"amount": 0}).status_code == 422
    assert client.post(path, headers=headers, json={"amount": "2"}).status_code == 422
    reply = client.post(path, headers=headers, json={"amount": 2})
    assert reply.status_code == 200 and reply.json() == {"value": 2}
    assert client.get("/api/v1/counter", headers=headers).json() == {
        "value": 2, "reader": "example-client",
    }
    operation = client.get("/openapi.json").json()["paths"][path]["post"]
    assert "requestBody" in operation
assert lifecycle == ["reader started", "reader stopped"]
```

This exercises ASGI authentication, request/model validation, handler context,
response validation, dependency order and shutdown. It does not open a TCP
listener or test identity provisioning. The Notebook tests additionally verify
SQLite reopen, session separation and calls from actual subprocess workers.

## ServerExtension fields

| Field | Behavior |
| --- | --- |
| `name` | Unique service identity; letters/numbers/underscores, except reserved `context`. |
| `service` | Existing object, used as supplied, not cloned. Mutually exclusive with factory. |
| `service_factory` | Sync class/callable or `module:factory` reference, resolved during build. |
| `scope` | `application` (default) shared service, or `session` per conversation. |
| `requires_services`, `optional_services` | Named dependencies and additional ordering constraints. |
| `endpoints`, `websockets` | `None` discovers on the service; `()` hides that surface; explicit tuple selects sources. |
| `critical` | Defaults true; false permits a runtime factory/start failure to remain visible without aborting by itself. |
| `start`, `stop`, `health` | Optional explicit `(service, service_context)` adapters, sync or async. |
| `middleware` | Tuple of `(middleware_class, options)` declarations. |
| `mounts` | Tuple of `(path, asgi_app, name_or_none)` declarations. Collisions are checked. |

Without a service/factory, an extension can expose explicit endpoint or socket
sources. Only decorated methods/declarations are discovered; ordinary methods
are not endpoints. Conventional socket discovery calls
`service.websocket_declarations()`; see [conversation transports](conversation.md).

## Factory injection and scope

Factory **parameter names** are service identities. A required named parameter
is a required dependency; one with a default is optional. `context` receives the
BuildContext instead. Keyword-only parameters work; positional-only parameters
and `*args` do not. A factory's `**kwargs` receives declared service dependencies,
not every BuildContext input. Additional explicit dependency names are used for
ordering but passed only if accepted by the signature or `**kwargs`.

String factories defer server imports until build, then undergo the same
signature analysis. Factories must return synchronously; asynchronous acquisition
belongs in `start()`. Optional absent dependencies are not passed, preserving the
Python default. A present optional dependency participates in ordering and must
start successfully for its consumer to start.

Constructor injection sees constructed instances **before startup**. Do not use
`context.services` to find them in a constructor: its read-only live view contains
only successfully started services. Use it later in deferred callbacks where
appropriate. Fixed paths/options are explicit `context.require(...)` and
`context.plugin(...)` reads, never guessed from parameter names.

Application services may depend only on shared services. Session services can
consume shared services and services from that session, never another session.
Inject `runtime` into a **session-scoped** service when it needs worker access.
Use `runtime.service(name)` for scoped operational lookup; the root
`application.service("runtime")` is invalid. On a session HTTP route,
`ctx.request.app.state.session` selects the controller explicitly. There is no
global current-session value. [Application ownership](application.md) explains
the root/default-session distinction.

For a dependency supplied by another plugin, declare both the service name and
a versioned [capability requirement](plugins.md#capabilities-are-contracts-services-are-objects).
Names/graph/capability validation happens before startup.

## Lifecycle and failure

`start()` and `stop()` may be synchronous or asynchronous. Explicit adapters take
precedence over conventional methods. A decorated HTTP method named `start`,
`stop` or `health` is not called as a lifecycle method; use an adapter if needed.

1. All factories run during build, in dependency order.
2. Lifespan starts services in that order and publishes each in the live context
   only after successful `start()`.
3. On critical startup failure or cancellation, already started services stop in
   reverse order. A failing service must clean up its **own partial start**.
4. Shutdown tries every started service in reverse order, removes each lookup
   even if stop fails, and preserves multiple failures in a BaseExceptionGroup.

A factory with `critical=False` may fail without exposing its routes, sockets,
mounts or middleware. Its error is retained in health. Failed dependencies also
make consumers unavailable; a critical consumer still aborts. Optional startup
failure similarly leaves the service unavailable. Declaration errors, missing
services, incompatible signatures and async factories are composition errors,
not optional degradation. Rebuild to retry construction; restarting the same
graph does not rerun a failed factory. Factory-body resource acquisition is the
factory's cleanup responsibility.

`await application.service_graph.health()` reports running/stopped/error/degraded
and optional probe details. A running probe raising, or returning a dict with
`status` of `error`, `degraded` or `unhealthy`, is degraded. `critical` determines
whether degraded service health affects aggregate health; it is not an automatic
restart policy. The health API can apply an application projection.

Services tied to canonical state may register suspend/resume callbacks using
`runtime.register_reset_hooks()`. These are separate from server lifecycle;
see [reset coordination](hosting.md#reset-coordination).

## HTTP request and response contracts

Import `endpoint` from `the_framework` or `the_framework.server`. Its arguments
are `method`, `path`, and keyword-only `request`, `response`, `authenticated`,
`authorization`, `context`, `status_code`, `request_encoding`.

- Authentication defaults **true**. An authenticated endpoint without a security
  policy fails registration. `authenticated=False` deliberately bypasses policy
  authentication and authorization for that route.
- `request`/`response` accept JSON Schema mappings or `modict` model classes.
  Without explicit schemas, supported signature annotations and YAML docstring
  schemas provide inference. Handler defaults are optional HTTP inputs (unlike
  strict model tool schemas). `request={}` explicitly permits dynamic payloads.
- Model request validation constructs the model (validators/defaults included),
  then passes its fields as keyword arguments, not as one DTO parameter.
  Response model construction/validation is enabled by default.
- `context="ctx"` removes that parameter from the wire schema and injects
  `EndpointContext(request, principal, application)`. The application is global;
  principal is None on a public route.
- `GET`, `DELETE`, `HEAD` parse query values; other methods parse JSON by default.
  Repeated query values become lists for declared array fields. Path fields are
  coerced and merged; repeating a path field in query/body is rejected.
- `request_encoding="multipart"` parses form/files under the application upload
  ceiling. It is not an arbitrary-body streaming endpoint; see [artifacts](artifacts.md).
- A returned Starlette `Response` bypasses JSON result validation. `None` with
  status 204/205/304 produces an empty response. Ordinary values become JSON.

OpenAPI adapts model field types, defaults/requiredness and extra-field policy.
It supports scalar JSON, literals, unions, lists, string-keyed dicts and nested
models. Arbitrary Python validators are enforced at runtime but not translated
into schema constraints. Default factories are not executed at registration.
Unsupported/recursive hints require explicit schemas; models are not Pydantic
wrappers and the adapter is not a general Python type serializer.

### Errors

`HttpError(status_code, code, message, details=..., retryable=False)` exposes a
controlled `{"error": {"code", "message", "retryable", ...}}` response. Do not put
secrets in it. Standard failures include authentication 401, authorization 403,
malformed JSON 400, unsupported content type 415, invalid input 422, multipart
ceiling 413 and invalid endpoint result 500. Upstream exceptions recognized by
the registry become retryable 502 errors; unexpected failures are logged with a
correlation id and returned as generic 500 errors, not raw tracebacks.

Sources: `server/composition/{declarations,application,services}.py`;
`server/api/{declarations,endpoints,uploads}.py`, `utils/model_schema.py`.
