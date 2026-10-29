# Reference

## Agent programming

| Topic | Contract |
| --- | --- |
| [Agents and specialists](agents.md) | Recipe construction, resources, sync/async turns, events, private task requests and result handling |
| [Tools](tools.md) | Schema inference, strictness, feedback, namespaces, execution, typed outputs and failures |
| [Extensions](extensions.md) | Configuration/state persistence, bindings, instructions, slash commands and lifecycle hooks |
| [Providers](providers.md) | Sampling, channels, ephemeral outputs, errors and context budgets |
| [Sessions](sessions.md) | Canonical data, storage, retention, archives and independent hosted conversations |
| [Context](context.md) | Model projection, recovery, token estimates and compaction |

## Server and clients

| Topic | Contract |
| --- | --- |
| [Application](application.md) | Build/compile/lifespan, process singleton, shared and session ownership |
| [Plugins](plugins.md) | Feature identity, options, dependencies/capabilities, installation versus agent binding |
| [Services](services.md) | Dependency injection, scopes, lifecycle, health, HTTP schemas and route discovery |
| [Hosting](hosting.md) | Session runtime, supervised workers, bridge and desktop parent |
| [Conversation transports](conversation.md) | HTTP commands/files, socket envelopes, reconciliation, ingress and restart barriers |
| [Events](events.md) | Typed lifecycle and streaming events; provisional versus committed output |
| [Artifacts](artifacts.md) | Durable files/presentations, vision input and client rendering |
| [Security](security.md) | Access policy, authentication, authorization and WebSocket handshakes |
| [Surfaces](surfaces.md) | Editable UI declarations, candidate builds, previews, publication and rollback |

## Bundled integrations and operations

| Topic | Contract |
| --- | --- |
| [Primitives](primitives.md) | Reusable services/utilities and bundled feature entry points |
| [Execution](execution.md) | Explicit local/host process channels, Bash edits/commands/jobs, bounds and cleanup |
| [Browser](browser.md) | Observation, tabs, UI access and browser RPC |
| [Realtime](realtime.md) | Voice projection, temporary connection and canonical continuity |
| [Work and scheduling](work-and-scheduling.md) | Goals, tasks, wakes, response waits and delivery evidence |
| [Deployment](../deployment.md) | Optional immutable backend build/test/select/restart and recovery |
| [Examples](examples.md) | Runnable examples, verification coverage and larger consumers |

## Imports and source ownership

Use the root package for declarations:

```python
from the_framework import (
    AgentExtension, AgentResources, AgentSpec, Application, BuildContext,
    Capability, CapabilityRequirement, ClientSurface, Plugin, QueuePolicy,
    ServerExtension, Session, agent_trigger, command, endpoint, hook, provider,
    tool, websocket,
)
```

Import specialized objects from their owning facade, for example
`from the_framework.agent import FunctionCall, ToolOutput, AgentResult`,
`from the_framework.server import SessionRuntime`, or a bundled plugin from
`the_framework.plugins.<feature>`. The root facade is not a flat export of all
runtime internals. Runtime wire payloads are not alternative composition APIs.

| Package | Responsibility / public entry point |
| --- | --- |
| [`the_framework`](../../the_framework/__init__.py) | Concise application/agent declarations and decorators |
| [`the_framework.agent`](../../the_framework/agent/__init__.py) | Agent, sessions/context, tools/providers, typed content, worker requests/events |
| [`the_framework.server`](../../the_framework/server/__init__.py) | Composition, runtimes, route declarations, security and client infrastructure |
| [`the_framework.plugins`](../../the_framework/plugins/) | Optional reusable features; complete plugin declarations, not product identity |
| [`the_framework.execution`](../../the_framework/execution/) | Explicit local/host execution and process control |
| [`the_framework.deployment`](../../the_framework/deployment/) | Optional immutable backend/supervisor mechanisms |
| [`the_framework.utils`](../../the_framework/utils/) | Atomic persistence, token estimates, IDs and shared small utilities |

`modict` supplies structured declaration/configuration/payload models;
`codex-backend-sdk` supplies the backend adapter. Their APIs and credentials are
owned by those libraries. The Framework does not promise every backend accepts
every model field, hosted tool type or model name shown in source defaults.

For a guided implementation use [First application](../guides/first-application.md),
[Notebook](../guides/first-plugin.md), or
[client integration](../guides/client-integration.md). [Historical notes](../history/README.md)
are evidence of earlier design work, not current contracts.
