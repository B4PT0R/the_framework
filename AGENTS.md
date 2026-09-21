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
- An `AgentApplication` has one durable primary conversation. The worker is its
  only history writer. Plugins may declare private specialist agents, but a
  specialist must not write into primary history; results return through the
  owning plugin or an ordered primary-agent input.
- The server owns authentication, routes, client connections and worker
  supervision. Workers own inference, tools and their sessions. Clients own
  their local interface and media/device execution. Do not move a
  responsibility across these process boundaries merely to simplify a call.
- Treat saved sessions, task delivery, HTTP/WebSocket payloads and client
  security as contracts. Preserve them when refactoring, or provide an explicit
  tested migration. A client reconnection or worker restart must not create a
  second main conversation or bypass safety controls.
- Plugins contribute through the framework's declarations and lifecycle, not
  by mutating FastAPI internals or global registries. Loading a plugin is
  separate from exposing its tools to an agent; server routes may remain
  available when an agent binding is disabled.

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
uv run pytest -q tests/application tests/agentic
```

`tests/application/` covers observable contracts and includes wheel-only
checks. `tests/agentic/` covers internals that may evolve. For changes to the
starter UI, also run its npm tests and build as described in
[starter/README.md](starter/README.md).
