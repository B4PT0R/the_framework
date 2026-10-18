# Working on The Framework

The public Python package is `the_framework/`; `starter/` is an editable
example that uses it. Read [README.md](README.md) for the first-run path and
[docs/framework.md](docs/framework.md) for the composition API. This
file records the constraints to preserve when changing the framework.

## Ownership and contracts

- Keep application identity, instructions, policies and domain-specific
  features outside `the_framework`. Dependencies point from applications to the
  framework, never the reverse. The starter must depend on `the_framework`, not
  on any particular application.
- `Application` is the global application object and server resource owner.
  `build()` returns its ASGI adapter, with `state.application` pointing to that
  same object. The lifespan reserves `Application.current()` before service
  startup and releases it on shutdown, startup failure or cancellation. Exactly
  one application may run in the process, including across server threads; a
  running application cannot rebuild itself. Construction/compile/imports remain
  inert, including in worker subprocesses. ApplicationHost and child Application
  clones are gone. `context.application` provides the owner during construction;
  `Application.current()` is available only during the live lifecycle.
- Application configuration stays in declared fields; runtime metadata is separate.
  `extensions` contains resolved declarations; `access_policy` is the effective
  server policy. `SessionRuntime` owns the worker/fleet/events, scoped services
  and ASGI routes for one conversation. `Session` stays canonical data. All
  sessions point to the same Application through `runtime.application` and
  `runtime.context.application`; routes expose `state.session` explicitly.
  Never add a global current-session pointer. `agent.bridge`/`runtime.bridge`
  remain the capability channels. Advanced `session_factory`/`session_extensions`
  run per session and must not reuse a live controller. Application services
  remain shared, session services/bindings/health remain isolated.
- An application with a default agent may host independent concurrent sessions. Each session has exactly one worker/history writer and owns its durable
  configuration, extension state and plugin bindings. Private specialists are agents
  with their own session; they never write into the caller history. Results return
  as ordered caller input; transient activity is routed only to that caller. The
  Harness may retain its single-conversation product policy.
- The server owns authentication, routes, client connections and worker
  supervision. Workers own inference, tools and their sessions. Clients own
  their local interface and media/device execution. Do not move a
  responsibility across these process boundaries merely to simplify a call.
- Keep session, task-delivery, HTTP/WebSocket and client-security contracts
  coherent across the Framework, starter and Harness. During this experimental
  phase the test data is disposable: update all consumers together and remove
  obsolete paths, schemas and aliases instead of accumulating compatibility or
  migration code. Preserve old data only when Baptiste explicitly requires it.
  When needed, migrations are standalone, one-shot scripts under `migrations/`,
  removable after execution; never embed them in runtime/startup code.
  Ordinary reconnection/restart must still resume the canonical conversation
  and enforce authentication; disposable test data does not weaken those features.
- Plugins contribute through the framework's declarations and lifecycle, not
  by mutating FastAPI internals or global registries. The complete declared
  plugin graph starts with the server and keeps its routes and services until
  shutdown; removing a runtime requires a new composition and server restart.
  Only a plugin's binding to an agent changes live. Disabling that binding must
  leave the plugin's server routes and services available.

## Agent construction and specialist catalogue

AgentSpec owns declarations; AgentTrigger owns task selection and result handling.
WorkerProfile/AgentTask/AgentResult remain wire payloads, not declarations to redesign.
Construction priority is declaration < resource defaults < resolved launch profile
< saved Session configuration; preserve executable resources/factories/initializers.
AgentSpec does not retain compiled profiles. Compilation retains private declarations;
the worker resolves one catalogue after extension load, including configured inactive
triggers and explicit specialists without handlers, then emits its wire profiles.
Use submit for nonblocking work and dispatch internally for results. Do not restore
call/publish aliases or a second profile catalogue.

## Agent context and compaction

Session owns canonical data and atomic mutations; Context owns model projection,
canonical preparation and compaction. Use agent.compact() for the current session,
not a separately assembled controller. Context.project is pure; canonical input
preparation performs recovery before projection. Generation adds ephemeral providers;
compaction never samples/persists them. Client archive and Realtime projections stay
separate. SessionProjections owns the standard full/display/activity views; the HTTP
history defaults to display. Register only application-specific projections.
SessionRuntime.reset_session is the application reset hook, unavailable by default
even if a low-level reset transaction factory exists; do not restore reset_handler
plumbing in Application.build. Keep the Compaction item/response payloads and session schema unchanged.

## Service construction

Declare Plugin.server_extensions explicitly and use Plugin.configure(context) only
for declaration-time contributions or dynamic groups. Bare plugin services and the
overloaded server_extension field are gone. Prefer a direct ServerExtension service
factory over nested plugin/service builders.
Factories request `context` for BuildContext inputs and name services in their other
parameters. Use `module:factory` references for server-only imports; resolve them
before dependency ordering, never during worker compilation. Decorated HTTP methods
and websocket_declarations are discovered by default; explicit empty tuples keep
surfaces private. Retain plugin builders for declaration-time contributions/dynamic
composition, sealed before service construction. Inputs, started services and saved
Session state remain distinct.

## Making changes

- Keep the public API small and typed. Prefer improving an existing primitive
  to adding a forwarding layer. Migrate internal callers when an internal API
  changes rather than retaining obsolete aliases.
- Use `modict` for structured models where its mapping and validation features
  fit. Inspect its API before adding a parallel model or conversion layer.
  Backend-specific Responses behavior belongs at the `codex-backend-sdk`
  boundary, not in ad hoc transport code elsewhere.
- Changes to shared dependencies such as `modict` or `codex-backend-sdk` must
  remain generally useful and backward-compatible; test them in their own
  repositories before relying on the change here.
- Keep the starter runnable from an installed wheel outside this checkout.
  Its source and interface must remain editable application code, not hidden
  framework policy.

## Verification

Add focused tests for the behavior being changed. Then run the framework suite:

```sh
uv sync --extra dev
uv run pytest -q tests/general tests/agentic
```

`tests/general/` protects deliberately durable behavior and API/application
contracts, including wheel-only checks. `tests/agentic/` provides empirical
feedback on evolving implementation choices; review and remove obsolete or
redundant checks periodically. Classify by the guarantee and intended lifetime,
not by test technology or authorship. Promote useful agentic tests when their
contract is intentionally made durable. See [tests/README.md](tests/README.md). For changes to the
starter UI, also run its npm tests and build as described in
[starter/README.md](starter/README.md).

Events belong to the component that owns their behavior. The worker core declares
only events needed for its generic operation. Events used only within a plugin's
feature lifecycle must be declared and emitted by that plugin. Public consumers
receive typed Framework/plugin events; backend wire events remain inside their
respective adapters and must be explicitly converted or filtered.

Session ServiceContext borrows only application-scoped services through its parent
graph. Its ServiceGraph owns only local lifecycle entries; inherited resources keep
their original owner and health probe, without no-op start/stop wrappers. Do not cache
configure hooks across sessions: their contributions may depend on session inputs.
