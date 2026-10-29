# Continuity: keeping the conversation through changes

[Documentation](../README.md) / Understand / Continuity

You can ask for a change, discuss it, replace the interface or restart the
backend, then continue the exchange. The application supports this by saving
the conversation separately from the processes and connections running it.

## Follow a saved conversation

When a worker commits a conversation item, it becomes part of the canonical
`Session` record. The session also holds saved configuration, extension state
and agent bindings. A file-backed session writes this state to disk; hosted
sessions use durable storage by default.

After a restart, the application opens that record, constructs the agent and
resumes execution. A reconnecting client retrieves a projection of the saved
history. The browser and the previous worker can disappear while the record
remains available.

For a deliberately temporary agent, `Session()` provides an in-memory session.
Retaining context between tasks and persisting it between process lifetimes
are separately configurable choices, particularly for specialists.

## What happens when you change something?

| Change | How the conversation continues |
| --- | --- |
| Refresh or reconnect a client | It retrieves retained history from the server; local drafts follow the client’s own saving policy |
| Publish a managed interface release | The client loads a new UI while the session stays with the application; rollback restores the preceding UI release |
| Replace an interrupted worker | The supervisor reconstructs execution around the canonical session; unfinished tool work needs inspection before retrying |
| Replace the server through its supervised host | Resources are rebuilt from declarations and saved data; the starter’s desktop parent retains its browser and listener |
| Compact the model context | The model receives a shorter working context while archived conversation remains available through history projections |

An active voice call has a temporary connection and needs its own cleanup and
recovery when execution changes. Durable conversation and reconnection provide
the basis for resuming the exchange.

## Keep history and working context distinct

The session stores the conversation. `Context` prepares what the model receives
for its next turn: active history, instructions and fresh provider output. A
provider might supply the current browser page or a status snapshot. Its next
output can replace the previous one.

Compaction summarizes working context to make room for further turns. The
archive preserves the earlier exchange, but the model’s next input contains a
selection of that information. When a temporary observation matters for later
work, save the useful conclusion in the conversation, memory or another durable
owner before closing its source.

This gives different storage roles a clear purpose:

| State | Examples | How to preserve it |
| --- | --- | --- |
| Canonical session | Conversation, settings, bindings, compaction archive | Keep the session’s durable storage |
| Feature-owned data | Memory, registry, scheduler records, managed surface releases | Include each feature’s private store in backups |
| Temporary execution and observations | Processes, sockets, streamed text, browser DOM, voice calls | Reconnect or reconstruct; save needed conclusions explicitly |

## Know what has actually survived

Committed items are the replayable record. Streaming text and operational events
show work in progress. Clients recover history from that committed record rather
than replaying old streaming chunks as messages.

External effects have their own state: a command may have written a file before
the worker stopped, or a service may have accepted a request before the result
was saved. Inspect that state before retrying interrupted work. Atomic snapshot
replacement protects individual stores from partial writes; recovery across
plugin stores and external systems requires the application’s own checks.

## Plan recovery alongside activation

Keep private data across source and release changes, back it up, and test how
your host resumes it. For an interface change, retain the previous release.
For backend or storage changes, verify that the candidate can open the intended
data and keep an appropriate recovery path before activation.

Continuity rests on these concrete arrangements: durable records, replaceable
execution and a verified path back to work. Permissions, retention and backups
are application choices that should match its users and deployment.

**Next:** [Your first application](../guides/first-application.md) includes stopping
and reopening the starter. Exact contracts: [Sessions](../reference/sessions.md),
[Context providers](../reference/providers.md), [Artifacts](../reference/artifacts.md)
and [Deployment](../deployment.md).
