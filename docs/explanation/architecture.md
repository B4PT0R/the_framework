# Architecture and execution flow

The Framework separates declarations, live resources and canonical records.
An application can replace its interface or worker without making either one
owner of the conversation. This separation is a concrete ownership contract,
not a guarantee that arbitrary updates or external effects are recoverable.

## Three layers

| Layer | Objects | Responsibility |
| --- | --- | --- |
| Declaration | `Application`, `AgentSpec`, `Plugin`, `ServerExtension`, `ClientSurface` | Select recipes, features, scopes, dependencies and interface build inputs |
| Execution | `SessionRuntime`, supervisor, worker, services, client connections | Run resources for their declared lifetime and enforce process boundaries |
| Canonical data | `Session`, feature-owned stores | Preserve committed conversation/configuration/state and each feature's durable records |

`Application.compile()` resolves declarations without starting resources.
`build()` constructs the ASGI assembly and services; the ASGI lifespan starts
resources in dependency order and stops them in reverse. A worker imports the
application recipe without starting another server. Configuration inputs,
constructed resources and saved session data are distinct.

```text
Client (UI and device permissions)
  ↕ authenticated HTTP / WebSocket
Application (server process)
  ├─ shared service graph, access policy, client-surface releases
  ├─ SessionRuntime A: session services, event routing, supervisor/fleet
  │    ↕ worker protocol and correlated application bridge
  │    Worker A: Agent + extensions + Context → backend
  │      └─ canonical Session A (one writer)
  └─ SessionRuntime B: independently scoped resources
       ↕
       Worker B → canonical Session B
```

Private specialists have their own sessions/workers. Their results become
ordered input to the caller; they never write the caller's history directly.
Transient specialist activity is routed only to the caller and is not itself
canonical conversation.

## Follow one text turn

1. A client submits an authenticated request. The host's chat ingress coordinates
   text/file acceptance and optional voice delegation, then submits a command to
   the selected runtime. Acceptance is not completion.
2. The runtime queues the worker command. The worker records command status and
   appends accepted canonical input. Streaming events can begin before an answer
   is committed.
3. `Context` repairs incomplete canonical tool protocol, adds context-prefix
   contributions and samples current providers for generation. Request preparation
   combines these with agentic instructions, schemas and configuration. Threshold
   compaction can replace active history before generation.
4. The worker adapts backend stream events to Framework events and canonical
   response items. Tool execution validates arguments and records both execution
   acknowledgements and typed outputs. Tools needing server resources call the
   application bridge; they do not reach into server memory.
5. Further model steps consume the committed results and fresh provider samples.
   The turn ends, is interrupted or fails; clients receive typed lifecycle events.
6. A reconnecting client retrieves retained archive pages through a display
   projection. It does not reconstruct committed history by replaying old token
   deltas. The model projection and client display projection are different views.

The deterministic [agent example](../reference/agents.md) runs this loop without
backend inference. The [Notebook tutorial](../guides/first-plugin.md) adds a
session SQLite service, HTTP API, bridge-backed tool, command and provider; its
tests also exercise two genuine worker subprocesses.

## Keep each owner explicit

- **Server:** authentication, HTTP/WebSocket adaptation, client connections,
  supervision and application/session services. `Application.current()` exists
  only during the live application lifecycle. There is no current-session global.
- **Worker:** inference, tools, agent extension registries and the sole canonical
  session writer. `agent.bridge` is its correlated server capability channel.
- **Client:** presentation, local drafts and device/media execution. A connected
  browser is neither the history owner nor proof of user attention.
- **Plugin:** one feature's contributions and stores. Installation lasts for the
  server composition; disabling its agent binding hides tools/context without
  removing its server routes or stopping services.

Realtime adds a temporary voice connection and its own bounded projection.
Durable voice contributions join the canonical conversation through the worker;
the voice engine is not a second independent history owner.

## Source map

| Package | Main ownership |
| --- | --- |
| `the_framework.agent` | Recipes, extensions, model loop, sessions/context, worker protocol and fleet |
| `the_framework.server` | Composition, service graphs, APIs, local hosting, client surfaces |
| `the_framework.plugins` | Optional feature implementations on those contracts |
| `the_framework.execution` | Explicit local/host execution mechanisms |
| `the_framework.deployment` | Opt-in immutable backend build and supervisor mechanisms |
| `the_framework.utils` | Owner-neutral utilities, including atomic file replacement |
| `starter/` | Editable application policy, launcher and UI; not library defaults |

Application identity, prompts, product policy and interface choices belong in
application code. The library supplies reusable mechanisms rather than importing
one application's decisions.

**Next:** [Continuity and failure boundaries](continuity.md).
Exact contracts: [Application](../reference/application.md),
[Hosting](../reference/hosting.md), [Sessions](../reference/sessions.md),
[Model context](../reference/context.md).
