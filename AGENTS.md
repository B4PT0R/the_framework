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
- `Application` is a reusable immutable definition; `build()` creates an independent
  ASGI server, whose lifespan owns startup/shutdown. It integrates conversation
  when a primary agent is declared; a service-only app omits that agent. There is
  no second public conversation builder. `app.state.application` is its
  `ApplicationHost`, with `definition`, effective `security`, shared services and
  `primary_session`. `SessionRuntime` owns one conversation's worker/fleet/events;
  `Session` remains canonical data. The agent/server capability channel is `bridge`,
  not another object named application. Advanced `session_factory` and
  `session_extensions` run for every session and must not reuse a live controller.
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
