# Plugin composition

[Documentation](../README.md) / [Reference](README.md)

A plugin packages one feature across the agent and backend: its tools, context,
services, routes and any private specialists. The application installs the
feature once and resolves the contributions and dependencies it declares.

Start with [Your first plugin](../guides/first-plugin.md) to add one tool. The
contracts here cover growing that feature, sharing capabilities and controlling
its binding to individual agents.

## On this page

- [Pairing the worker and server sides of a plugin](#pairing-the-worker-and-server-sides-of-a-plugin)
- [Customizing a declaration](#customizing-a-declaration)
- [Agent-only shorthand](#agent-only-shorthand)
- [Feature identity](#feature-identity)
- [Capabilities and dependencies](#capabilities-and-dependencies)
- [Construction inputs and service lookup](#construction-inputs-and-service-lookup)
- [Build-time contributions](#build-time-contributions)
- [Installation, bindings and runtime status](#installation-bindings-and-runtime-status)

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
| `Plugin.specialists` | Private `AgentTrigger` invocations; bare `AgentSpec` uses the default policy |
| `Plugin.provides_capabilities` | Versioned contracts exported by this feature |
| `Plugin.requires_capabilities` / `optional_capabilities` | Required or optional versioned contracts from other plugins |
| `Plugin.agent_binding_enabled` / `agent_binding_required` | Initial and mandatory binding to each public agent; server installation remains fixed |
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
    name="Example", version="1", agents=(primary,),
    plugins=(BASH, MEMORY, SCHEDULER, SYSTEM),
)
```

Bundled feature packages export `AGENTIC`, `ARTIFACTS`, `BASH`, `CHROMIUM`, `MEMORY`, `REALTIME`,
`REGISTRY`, `SCHEDULER`, `SYSTEM` and `WEB_SEARCH`. Each is an immutable `Plugin`
declaration containing its complete feature: agent binding, private specialists,
server services, lifecycle and capabilities where applicable. Importing or
compiling these declarations does not import their server implementations.
Scheduler and system services require the application's `runtime` service;
`data_root` in `BuildContext` supplies their default storage directory. Optional
plugin options `scheduler.state_path` and `system.state_path` override the files
through `BuildContext(plugins={"system": {"state_path": path}})`. System restart
uses the runtime's application-owned `restart_server` operation when configured.


## Customizing a declaration

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

## Agent-only shorthand

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

## Feature identity

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

## Capabilities and dependencies

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
## Construction inputs and service lookup

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

## Build-time contributions

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

## Installation, bindings and runtime status

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

---

[Reference index](README.md) · [Documentation paths](../README.md)
