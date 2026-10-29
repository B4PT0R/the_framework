# Architecture: assembling the application

[Documentation](../README.md) / Understand / Architecture

An application needs an agent that can act, features it can use, an interface
for the user and a place to keep their conversation. The Framework gives each
of these a building block and assembles their running resources together.

## Start from the application you want

The starter combines a Python backend with a React interface in Chromium.
Its application declaration chooses an agent recipe and installs features such
as tools, memory and scheduling. A separate server factory supplies the private
data location and access policy.

The first declarations you work with are:

| You want to define… | Building block | What you put there |
| --- | --- | --- |
| The application as a whole | `Application` | Agent recipes, installed features and server/UI composition |
| An agent’s behavior | `AgentSpec` | Instructions, model defaults, extensions and resources |
| A feature | `Plugin` | Its agent tools and any backend services or specialists |
| The editable interface and its releases | `ClientSurface` | Source location, build command, output and serving route |

An `AgentExtension` implements the agent side of a feature, such as tools or
context providers. A `ServerExtension` declares a backend component when the
feature needs one. A small tool can start with the agent side alone; the
[first-plugin guide](../guides/first-plugin.md) shows that path.

## Give the conversation a lasting home

The running application stores each conversation in a `Session`: its history,
saved settings, extension state and plugin bindings. A supervised worker runs
the agent’s turns and writes that record. `Context` prepares the input for the
model from the session and current observations.

On the server, a `SessionRuntime` connects the conversation to its worker,
services and transports. The application builder creates this assembly for you.
An application can host several independent conversations using the same agent
recipe, each with its own worker and saved state.

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
```

The server owns authentication, client connections and supervision. Workers run
inference and tools. Clients display the conversation and handle device-local
interactions such as microphone permissions. Because the saved conversation
belongs to the application, a client can reconnect and an interface can change
without becoming a new conversation.

## Add a feature as one coherent unit

A feature may begin with a tool and later need a settings endpoint, shared
state or a private helper agent. A `Plugin` groups these contributions so the
application can resolve dependencies and start their services in order. Shutdown
runs in reverse order.

Installation puts the feature’s runtime in the application for its lifetime.
An agent binding controls which tools and context that agent receives. You can
change a binding live while retaining the feature’s settings routes and stored
state. To remove the installed runtime, change the composition and restart.

Private specialists are agents with their own sessions. They return results to
the caller, whose worker remains responsible for writing its conversation.
Applications choose whether each specialist retains context between tasks or
stores it across restarts.

## Prepare the system before starting it

The application module declares the composition. Imports and compilation are
inert, so a worker can load its agent recipe without starting another server.
This also makes it possible to inspect the composition before activating it.

`compile()` validates declarations. `build()` resolves the server assembly using
explicit inputs from `BuildContext`, such as paths and plugin options. The ASGI
lifespan starts services and stops them on shutdown. During that live lifecycle,
`Application.current()` identifies the application; requests select their
conversation explicitly.

The starter’s desktop parent keeps the browser and listening socket while
replacing its server child. Other hosts can choose a different launcher while
using the same application and session contracts.

## A conversation through the system

1. A client submits text or files through an authenticated transport.
2. Host-owned chat ingress coordinates acceptance with optional voice mode.
3. The session runtime queues work for the supervised worker.
4. The worker prepares model input from canonical history and selected providers,
   then runs the turn and tools. A plugin may submit private specialist work.
5. The worker commits canonical items. Typed events report activity and
   provisional streaming output as the turn progresses.
6. Clients display committed history and artifacts. On reconnect they retrieve
   retained state from the server.

Voice adds a live call with its own temporary connection and context projection.
Its durable contributions join the same canonical conversation.

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

Applications depend on the Framework’s public contracts. Keeping their specific
instructions, design and permissions in application source makes the same
foundation reusable across different products.

**Next:** [Continuity](continuity.md) follows the saved conversation through a
change, or try [Your first application](../guides/first-application.md).
For exact lifetimes, see [Application reference](../reference/application.md).
