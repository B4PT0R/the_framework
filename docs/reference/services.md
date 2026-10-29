# HTTP services and lifecycle

[Documentation](../README.md) / [Reference](README.md)

A backend service gives a feature a long-lived resource or an HTTP API. The
Framework constructs services from declared dependencies, starts them in order
and exposes their declared routes with validated inputs and outputs.

Begin with the greeting service below, then add state and lifecycle as needed.
For a tool that only runs inside the agent, follow
[Your first plugin](../guides/first-plugin.md).

## On this page

- [The small path](#the-small-path)
- [Adding state and lifecycle](#adding-state-and-lifecycle)
- [Model-based endpoint contracts](#model-based-endpoint-contracts)
- [Service lifecycle](#service-lifecycle)

## The small path

A complete HTTP service can be assembled without manipulating FastAPI or a
lifecycle callback. This deliberately public greeting is a small local example,
not an authentication policy for a private agent application:

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

---

[Reference index](README.md) · [Documentation paths](../README.md)
