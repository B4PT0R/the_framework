# Examples and verification

## Notebook: one feature across the stack

[Source and run instructions](../../examples/notebook/README.md) ·
[step-by-step guide](../guides/first-plugin.md)

The Notebook feature combines:

- strict request models shared between HTTP and the worker bridge;
- session-scoped SQLite storage, upsert, revision and lifecycle;
- authenticated GET/PUT routes and OpenAPI;
- worker tools for save/list, persisted `/project` selection and bounded settings;
- an async provider that samples recent titles rather than repeating bodies;
- a versioned capability and deferred server import.

Verification from the checkout (no account, inference or external service):

```sh
python -m pytest -q tests/general/test_documented_notebook.py
```

The tests exercise storage reopen and validation, real tool-output processing,
state/config persistence, HTTP auth/validation/lifecycle, and **two real worker
subprocesses** with deterministic Responses streams. Worker saves are visible
over HTTP; the second session has separate storage; disabling a binding keeps
the installed routes and store alive. A fake backend does not validate real model
selection or authentication. The example modules are source examples, not
installed wheel packages.

## Standalone agent: a complete tool turn

[Agents](agents.md#execute-a-complete-turn-without-a-model-account) contains a
copyable program that sends a scripted function call through real schema
validation/execution/history, then generates an answer from a second request.
The page also executes async consumption, saved-configuration precedence and
private specialist submit/dispatch. The latter is explicitly in-process, not a
fleet integration test.

Every Python block in that page and in [Tools](tools.md),
[Extensions](extensions.md) and [Providers](providers.md) is executed in order by
the documented-contract test. It also covers the root composition/import examples,
server references, session/context, events/security, media/surfaces, bundled
primitives, execution, scheduling and deployment:

```sh
python -m pytest -q tests/general/test_documented_agent_contracts.py
```

## Service-only application

[`minimal_agent_app.py`](../../examples/minimal_agent_app.py) is named historically:
it is a **service-only** application, with no recipe, worker or inference.
It provides a validated counter endpoint, service lifecycle and graph health.
From a checkout where the Framework and Uvicorn are installed:

```sh
uvicorn examples.minimal_agent_app:app --host 127.0.0.1
```

Use its documented example bearer credential only on loopback. It is hard-coded
for demonstration, not a credential-management design. The counter is in memory;
it is not durable storage or a concurrency-safe database.

## Editable starter and larger consumers

The [starter](../../starter/README.md) supplies an editable desktop application:
agent composition, local security, launch supervision, React UI, attachments,
voice and managed surfaces. It is copied into a consumer's source by bootstrap.
Its persona, instructions, access policy and chosen features are application
choices, not immutable library behavior.

The separate **The Harness** project is a larger consumer of the same
`Application`, `AgentSpec` and `Plugin` contracts, including multi-device
interfaces and application-specific integrations. It is not imported by the
Framework and is not required to run these examples. Use its current checkout
as a consumer example, not as the API specification for this library.
