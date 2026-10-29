# Larger composition examples

[Documentation](../README.md) / [Reference](README.md)

Use this to locate complete applications and understand their relationship to the library. The Harness is a separate consumer, not a dependency of this package.

## Standalone example

[`examples/minimal_agent_app.py`](../../examples/minimal_agent_app.py) is a second,
independent application that imports only `the_framework`. It demonstrates an
authenticated endpoint, a lifecycle-managed service and graph-derived health
without importing any The Harness product module:

```bash
uvicorn examples.minimal_agent_app:app
```

## The Harness as a larger application example

The separate The Harness application uses the same `Application` and
`Plugin` API as the starter. Its declaration supplies one public agent recipe,
product plugins, their versioned dependencies and a multi-device client
surface. The server entrypoint supplies process-local inputs through
`BuildContext` and calls `build`, which owns the canonical runtime
assembly.
Each installed plugin constructs its own services and routes through the shared
dependency graph: for example, the Realtime plugin owns its controller-facing
APIs and shared call state, while paired-client transport and media services
belong to their respective plugins. Removing a required capability fails
compilation before the server starts.

This division is useful when building a larger application: the top-level
assembly names the features and provides resources, while each feature owns its
HTTP, WebSocket, service and agent contributions. The framework does not import
application-specific policy.

## Design rule

The framework follows progressive disclosure:

- **simple:** `Application`, `ServerExtension`, `Plugin`, conventional
  service lifecycle;
- **composable:** capabilities, dependencies, decorated endpoint objects,
  middleware, mounts, named projections and plugin specifications;
- **advanced:** explicit lifecycle adapters, authorization policies and custom
  response objects.

Adding power must not make the small path more ceremonial. An application
feature belongs in the core only when it is a reusable orchestration primitive;
identity, prompting, device semantics, media policy and product UX remain in
the application's extensions.

---

[Reference index](README.md) · [Documentation paths](../README.md)
