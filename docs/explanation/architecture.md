# Architecture: components and choices

[Documentation](../README.md) / Understand / Architecture

## One system, several owners

Think of the application as a place where a conversation lives, not as the
browser window currently showing it.

```text
Application (server process)
├── shared services and client-surface releases
├── SessionRuntime: conversation A
│   ├── worker supervisor → worker process → agent + Session + Context
│   └── session-scoped services and transports
└── SessionRuntime: conversation B (when requested)
    ├── its own supervisor → its own worker and Session
    └── its own session-scoped services

Clients ↔ authenticated HTTP / WebSocket ↔ server
Private specialists: separate agents/sessions, results routed to their caller
```

This is an ownership map, not a promise that every deployment uses the same
launcher or process layout. The starter adds a desktop parent that keeps the
browser and listening socket while replacing its server child.

## The components you encounter first

| Component | Role | Not to be confused with |
| --- | --- | --- |
| `Application` | Declares the system and owns its server services, surfaces and session hosting | A conversation or a mandatory frontend |
| `AgentSpec` | A reusable recipe: instructions, defaults, extensions and resources | A running agent or its saved history |
| `Session` | Canonical conversation, settings, extension state and bindings | The browser’s display state or the entire model input |
| `Context` | Prepares model input from the session, adds current providers and coordinates compaction | An independent durable conversation |
| Worker | Executes turns and owns canonical writes for its session | An HTTP handler or browser process |
| `SessionRuntime` | Server-side controller for one conversation and its worker/services | A second Application |
| `Plugin` | One installed feature, including agent/server contributions as needed | Only a collection of tools |
| `AgentExtension` | Contributes tools, providers, hooks and behavior to an agent | The plugin’s installed server runtime |
| `ServerExtension` | Declares a backend component and its dependencies/routes | An agent extension class |
| `ClientSurface` | Describes editable UI sources and managed releases | The history’s owner |

You do not instantiate every row yourself. The hosted application builder
assembles the usual worker, runtime and transports; advanced contracts remain
available when a composition needs them.

## Why separate declarations from running resources?

An application module declares what exists. Importing or compiling it must not
launch services, workers, browsers or inference. The worker can import that same
module safely to find its agent recipe without starting another server.

`compile()` validates composition without running service factories. `build()`
resolves the server assembly; the ASGI lifespan starts and stops services.
Construction inputs are explicit in `BuildContext`, so paths and plugin options
are not confused with live services. Deferred factory references keep server
implementation imports out of the worker when needed.

The benefit is inspectable composition and predictable startup failures. The
cost is a distinction between a declaration, construction and activation that
small scripts often leave implicit.

## Why separate the server, worker and client?

The server authenticates clients, owns transports and supervises execution.
The worker runs inference and mutates its canonical session. Clients display
projections and own local interactions such as microphone permissions.

This makes a disconnected browser a client problem, not a lost conversation.
It also lets a worker be replaced without making a browser the source of truth.
The boundary does not make tools safe: a process still has its configured OS
permissions. Transport and lifecycle separation are not a security sandbox.

## Why one feature declaration instead of independent tool and route registries?

A feature may need tools, providers, server state, HTTP routes and a private
specialist. A `Plugin` owns those contributions together. Its services participate
in the same dependency graph as application services: providers start before
consumers and shutdown reverses the order.

Installation and exposure are distinct. Installing a plugin declares its server
runtime for the application lifetime. Changing its agent binding changes what a
particular agent can use; it does not tear down routes or delete state.
This keeps settings and safety controls available even when tools are disabled.
Remove the startup declaration and restart to remove the runtime.

Use an agent-only extension for a small tool. Add service dependencies or versioned
capabilities only when a feature actually crosses those boundaries.

## Why explicit conversations and private specialists?

Independent public sessions have separate workers, histories, settings and
bindings while sharing application-owned services. There is no global mutable
“current session” that a request can accidentally use. `Application.current()`
identifies the running application, not an implicit conversation.

A specialist is another agent with its own session and invocation policy. It
returns work to its caller rather than editing the caller’s history directly.
That preserves a clear canonical writer and lets applications choose whether
specialist context is task-local, retained or durable.

## A conversation through the system

1. A client submits text or files through an authenticated transport.
2. Host-owned chat ingress coordinates acceptance with optional voice mode.
3. The session runtime queues work for the supervised worker.
4. The worker prepares model input from canonical history and selected providers,
   then runs the turn and tools. A plugin may submit private specialist work.
5. The worker commits canonical items. Typed Framework events report activity
   and provisional streaming output; backend-specific signals stay at adapters.
6. Clients display committed history and artifacts. On reconnect they recover
   from retained state rather than treating old streaming chunks as history.

Voice adds a temporary live call and its own context projection. It does not
become an independent owner of the canonical conversation.

## Where these responsibilities live in source

| Package | Owns |
| --- | --- |
| `the_framework.agent` | Sessions, context, extension contracts, agents and workers |
| `the_framework.server` | Composition, API adaptation, supervision and client surfaces |
| `the_framework.plugins` | Optional reusable feature implementations |
| `the_framework.execution` | Explicit local/host execution mechanisms |
| `the_framework.deployment` | Opt-in immutable backend construction and supervision |
| `the_framework.utils` | Small owner-neutral primitives such as atomic persistence |
| `starter/` | Editable application policy, launcher and interface |

An application depends on the Framework, never the reverse. Shared browser-side
packages and The Harness are separate projects, not hidden requirements of a
Python application using this library.

**Next:** [Continuity](continuity.md), or [Your first application](../guides/first-application.md).
For exact lifetimes, see [Application reference](../reference/application.md).
