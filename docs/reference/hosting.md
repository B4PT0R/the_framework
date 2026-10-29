# Workers, application bridge and host lifecycle

`Application.build()` assembles a runtime for each conversation. The server owns
HTTP/security/client connections and worker supervision; the worker owns inference,
tools and canonical session writes. Browser/device execution lives in the client.
These are process boundaries, not objects to pass through a global registry.

## Standard assembly

Use the starter's importable application and security policy (see
[first application](../guides/first-application.md)). An agent application's build
requires its `module:application` reference, `BuildContext(data_root=...)`, HTTP
security and authenticated socket handshakes. `handshake` supplies both defaults;
`event_handshake` and `application_handshake` override the channels separately.

The assembly creates a supervised primary worker, session runtime, plugin-binding
host, agent/health APIs and the events/application WebSockets. Build does not enter
lifespan, start inference, bind a listening port or perform account authentication.
With one public recipe selection is implicit; with several, use `agent=...`.
A saved default session selects its saved `agent_name`. Private specialists have
separate sessions and invocation policies, not public conversation identities.

Each runtime owns a bridge, worker/fleet, subscriptions, command correlation and
scoped service graph. Shared services start once in the Application graph;
closing one session stops only its local services and workers. Never reuse a
controller or scoped service object across independently hosted sessions.

## Application bridge

Worker code calls `await agent.bridge.call(capability, method, payload,
timeout_ms=30_000)`. A session service registers
`runtime.bridge.register(capability, handler)`. Its handler accepts
`(method, payload)` and returns JSON-compatible data, synchronously or
asynchronously. `contextual=True` adds `command_id`; `contextual="call"` adds the
complete `ApplicationCall` as `call`. These are opt-ins, not positional arguments.

The [Notebook service](../../examples/notebook/server.py) validates the same strict
models for HTTP and bridge calls, then offloads SQLite work with
`asyncio.to_thread`. Bridge payloads do **not** automatically undergo HTTP model
validation or authorization. The registering service owns those contracts.

Here is an isolated transport-contract test: an `ApplicationClient` emits the
real request model; `ApplicationBridge.dispatch()` routes it and returns a typed
result through a fake supervisor. This deliberately replaces IPC only. The
Notebook integration test covers actual subprocess workers.

```python
import asyncio
from the_framework.agent.runtime.bridge import ApplicationClient, ApplicationError
from the_framework.agent.runtime.protocol import ApplicationCall
from the_framework.server.runtime.bridge import ApplicationBridge

async def verify_bridge():
    replies = asyncio.Queue()

    class Supervisor:
        async def send(self, result):
            await replies.put(result)

    bridge = ApplicationBridge(Supervisor())
    client = ApplicationClient()
    received = []

    async def read_labels(method, payload, *, call):
        if method != "read":
            raise ValueError("unsupported operation")
        received.append((call.command_id, payload["project"]))
        return {"labels": ["published"]}

    bridge.register("labels", read_labels, contextual="call")

    async def request(packet):
        await bridge.dispatch(ApplicationCall(command_id="turn-7", request=packet))
        result = await asyncio.wait_for(replies.get(), packet.timeout_ms / 1000)
        assert result.id == packet.id
        if result.status != "success":
            raise ApplicationError(result.error)
        return result.result

    client.bind(request)
    await bridge.start()
    try:
        assert await client.call("labels", "read", {"project": "docs"}) == {
            "labels": ["published"],
        }
        assert received == [("turn-7", "docs")]
        try:
            await client.call("labels", "write", {})
        except ApplicationError as error:
            assert "unsupported operation" in str(error)
        else:
            raise AssertionError("handler error did not cross the bridge")
    finally:
        await bridge.stop()

asyncio.run(verify_bridge())
```

A worker bridge must be bound by its host; an unbound ApplicationClient raises
ApplicationError. The real worker waits under the request deadline and sends
ApplicationCancel when interrupted/timed out. Cancellation is cooperative, not
rollback of side effects already performed. Do not automatically retry a
non-idempotent operation merely because its response was lost.

`dispatch()` schedules calls independently of worker reception, so a slow async
handler need not block status or cancellation. The bridge bounds concurrent calls
and its queues (currently 128 each), rejects saturation, cancels running tasks on
ApplicationCancel and drains/cancels on stop or worker recovery. Handlers must not
block the event loop with synchronous work. Cancellation of `to_thread` does not
forcibly stop the already-running thread or undo its transaction.

For unhandled capabilities, registered routers may route to an authenticated
application client; absence of a connected client fails the call. The application
WebSocket is not a second canonical history writer. A server-side
`runtime.bridge.call(...)` may invoke a registered local handler directly; that
fast path is **not** wrapped in the forwarded-call timeout machinery. Design long
server operations with their own cancellation/deadline contract.

## Ordered worker mutations

Server services must not edit the worker's Session file. Use:

| Runtime method | Meaning |
| --- | --- |
| `update_config(updates, id=..., timeout=10)` | Ordered validated persisted mutation; returns `AgentConfigUpdated`, including config and `restart_required`. Missing required result raises `InvalidWorkerResult`. |
| `apply_event(name, payload, id=..., timeout=10)` | Wait for a durable ordered external event; returns its Event or None. |
| `submit(request)` | Send without waiting for eventual completion. |
| `command(request, timeout=10)` | Wait for the correlated command's completion/failure, returning its last CommandEvent payload. |
| `request(request, timeout=5)` | Wait for a direct correlated response, e.g. status; not a history mutation shortcut. |
| `publish(output)` | Broadcast to subscribers only; does not persist an event. |

Retain a stable id **and identical payload** for a command retry. A timeout is
not proof of failure before effects, nor do several separately submitted calls
become one atomic transaction. Ephemeral telemetry uses TransientEventRequest;
ordered external events use ExternalEventRequest. See [events](events.md).

Worker-output observers run in reception order. Their exceptions are logged
without discarding the response. They must not wait for a reply from that same
worker (which would require reception to advance).

## Customizing session assembly

Advanced `build()` inputs:

- `session_factory(supervisor, context)` constructs a fresh SessionRuntime per
  conversation. Do not retain a live controller globally.
- `supervisor` replaces the default worker host for the default session. Its
  session identity and declared application reference must match.
- `session_extensions` is a tuple or `callback(runtime)` returning per-session
  declarations, for example application-specific reset endpoints/middleware.
- `session_projection="display"` exposes user/assistant messages and artifacts.
  `"full"` is an explicit internal-history opt-in, not a privacy-neutral default.
- `health_projection(payload)` adapts diagnostics; `ApplicationHealthApi` includes
  readiness/activity, bridge connectivity, specialist status and service probes.

Set `BuildContext(restart=callback, ...)` to give the default runtime a supervised
restart operation. Runtime methods `restart_server`, `surface_notify` and
`surface_progress` support surface lifecycle automatically. Missing restart
capability must remain missing, not wrapped in a callback that fails later.

A `plugins.binding_update` contribution can supply a modality/policy barrier;
at most one coordinator is accepted. `plugin_binding_update` construction input
is the explicit override. The callback accepts `(name, enabled, *, call=None)`
and should delegate to `runtime.set_plugin_binding(..., call=call)`. The optional
call identifies a tool suspended waiting for this operation. Binding controls
have priority 5 versus ordinary command priority 10 and may run at that suspension
point, including before call transmission. A correlated call no longer waiting
fails rather than becoming a late mutation. The worker confirms activation and
persistence before the server updates its binding cache.

## Reset coordination

The generic `SessionRuntime.reset_session()` deliberately reports unavailable
(HTTP 501). The application selects its reset policy, normally by overriding that
method and using `reset_runtime_state(transaction_factory=...)`. A configured
low-level factory alone does not turn on a public destructive endpoint.

`FileResetTransaction(root, paths)` stages selected relative paths. They must not
overlap, escape the root or traverse directory symlinks. Failed worker restart
restores selected originals and removes newly created selected paths. This is
in-process rollback, **not** a crash-recovery journal or a user-data backup.
A per-call transaction factory receives the canonical session path; it does not
replace the runtime's default policy permanently.

`runtime.register_reset_hooks(suspend=..., resume=...)` requires at least one
callback, synchronous or async. Suspensions run in reverse registration order
before workers stop. Resumptions run in registration order after commit or
recovery. Failed suspension aborts and recovers previously suspended participants.
Voice/plugin-specific reconciliation belongs to that plugin's reset hooks, not
conditional branches in the application's reset handler.

Remote services may subscribe through `add_application_event_listener` and
`add_capabilities_changed_listener` (paired remove methods). Application-event
callbacks run synchronously after lease validation; capability-authority
callbacks may be async and are awaited in order. Failures propagate. Subscribe
at startup and unsubscribe at shutdown; authority policy belongs to the feature.

## Local desktop ownership

`the_framework.server.runtime.local.LocalServer` owns an HTTP **process** lease,
loopback listener, private discovery descriptor and credentials. Enter its context
before composing the app, pass `process.request_restart` as the BuildContext
restart callback, then await `process.serve(app)`. It returns 75
(`RESTART_EXIT_CODE`) after requested restart or zero on normal stop. Its context
releases discovery/listener/lease even when app construction fails.

A desktop supervisor owns replacement of that process. The starter retains an
inherited listener; each child closes its own copy, not the supervisor's. Another
supervisor can keep a fixed configured port so an existing browser reconnects.
`shutdown_event()` scopes SIGINT/SIGTERM handlers; `serve(parent_stream=...)` can
stop a child when its parent pipe closes. This code assumes an appropriate local
process/event-loop host; it is not a cross-platform deployment promise.

The browser RPC daemon separately owns Chromium contexts/tabs/unsent UI state.
LocalServer never closes that browser on backend replacement. The daemon's own
async lifecycle closes its resources at daemon shutdown. Reconnecting a browser
is not the same operation as resuming the canonical worker Session.

Process bearer credentials rotate; `ui-session.key` beside the discovery file
persists the browser credential. Policies validate each credential in its intended
channel. Do not print/copy private discovery records or bootstrap links into
conversation history. See [security](security.md), [browser](browser.md), and
[immutable backend deployment](../deployment.md).

Sources: `server/runtime/{session,bridge,local}.py`,
`agent/runtime/{bridge,worker,protocol}.py`, `server/composition/application.py`.
