# Hosted runtime and desktop lifecycle

[Documentation](../README.md) / [Reference](README.md)

Use this for the hosted worker/runtime assembly, desktop process ownership and
server-to-worker calls. The ordinary application builder already assembles these
parts; customize them only when your application needs a different policy.

## On this page

- [Conversation runtime integration](#conversation-runtime-integration)
- [Standard conversation assembly](#standard-conversation-assembly)
- [Local desktop process ownership](#local-desktop-process-ownership)
- [Application calls](#application-calls)

## Conversation runtime integration

For every hosted session, `Application.build()` installs one session-scoped
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

## Standard conversation assembly

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
`Application.agents` is the public catalogue of AgentSpec recipes. One recipe may
run in multiple independent sessions. The existing default session selects its
saved agent_name on resume. For a new default session, a single declared agent is
implicit; with several declarations, pass `build(..., agent="assistant")`.
Creating another session follows the same selection rule. Worker startup receives
the selected identity explicitly; private plugin specialists remain separate.
The worker creates each durable session's identity under its exclusive writer lock.
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

## Local desktop process ownership

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

A service-only `Application` leaves `agents` empty and uses
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

## Application calls

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

---

[Reference index](README.md) · [Documentation paths](../README.md)
