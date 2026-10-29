# Technical reference

[Documentation](../README.md) / Reference

Find the declaration or runtime contract for the feature you are implementing.
Each page starts with the job it supports, then gives the API details and examples.
For a guided implementation, begin with
[Your first application](../guides/first-application.md) or
[Your first plugin](../guides/first-plugin.md).

| You need to… | Read |
| --- | --- |
| Assemble an application and understand its lifetime | [Application](application.md) |
| Customize hosted runtime or desktop process ownership | [Hosting](hosting.md) |
| Add a backend service and validated HTTP routes | [Services and lifecycle](services.md) |
| Compose complete features and their dependencies | [Plugins](plugins.md) |
| Host independent conversations | [Sessions](sessions.md) |
| Construct an agent or invoke a private specialist | [Agents](agents.md) |
| Implement a client event stream | [Public events](events.md) |
| Define access policies and endpoint contracts | [Security](security.md) |
| Add generation-time information | [Context providers](providers.md) |
| Deliver feature updates during a voice call | [Context](context.md) |
| Publish files, images or widgets | [Artifacts](artifacts.md) |
| Reuse storage/configuration/lifecycle building blocks | [Application primitives](primitives.md) |
| Build, preview, publish or roll back an interface | [Client surfaces](surfaces.md) |
| Control browser observation, tabs or application UI access | [Browser](browser.md) |
| Integrate voice and its projection | [Realtime](realtime.md) |
| Submit text/files or coordinate text with voice | [Conversation ingress](conversation.md) |
| Schedule tasks, wakes or response waits | [Work and scheduling](work-and-scheduling.md) |
| Find a complete example or larger consumer | [Composition examples](examples.md) |
| Operate an immutable backend | [Deployment and recovery](../deployment.md) |

These pages describe the composition API in this checkout. During the alpha
phase, check the [changelog](../../CHANGELOG.md) when comparing a published wheel
with development sources. Historical design notes are [separate](../history/README.md).

## Public imports

The root namespace exposes the concise construction API: `Application`,
`BuildContext`, `AgentSpec`, `AgentResources`, `Session`, `AgentExtension`,
`Plugin`, `ServerExtension`, `ClientSurface`, `Capability`,
`CapabilityRequirement` and `QueuePolicy`; and the declaration decorators
`tool`, `provider`, `command`, `hook`, `agent_trigger`, `endpoint` and `websocket`.
Specialized controllers, payloads and runtime classes live in their owning
packages rather than being flattened into the root namespace.

The exports in [`the_framework/__init__.py`](../../the_framework/__init__.py)
are the authoritative root import surface. See [Architecture](../explanation/architecture.md#where-these-responsibilities-live-in-source)
for package ownership; reference examples using application-provided objects
assume those objects are defined by the caller.
