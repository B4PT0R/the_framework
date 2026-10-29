# Continuity: what persists, what is temporary

[Documentation](../README.md) / Understand / Continuity

“Persistent agent” does not mean one immortal process or one infinitely large
model prompt. It means the application keeps durable state and has explicit
ways to reconstruct execution around it.

## Three different kinds of state

| Kind | Examples | Consequence |
| --- | --- | --- |
| Canonical session data | Conversation, saved configuration, extension state, agent bindings, compaction archive | A file-backed session can resume without relying on client memory |
| Feature-owned durable data | Memory stores, registry, scheduler records, managed surface releases | Each owner has its own storage/lifecycle; back up the application’s private data, not just chat text |
| Temporary execution and presentation | Worker processes, sockets, streamed deltas, provider snapshots, browser DOM, voice calls | Reconnect, rebuild or explicitly preserve needed conclusions; do not mistake these for saved history |

`Session()` can also be in-memory. Persistence is a hosting/storage choice, not
a magical property of every agent instance. Hosted sessions are durable by
default. Retaining context across tasks and storing it across process restarts
are separate choices, especially for specialists.

## Canonical history is not model input

The session is the record. The `Context` prepares what the model receives:
active history, instructions and current provider output. Providers can supply
fresh information without writing it to the conversation. Their output may be
replaced or disappear on the next generation.

Compaction reduces the active model context while retaining archived conversation
for history projections. It does not erase the canonical archive, nor guarantee
that every detail is present in the model’s next input. If a temporary observation
matters later, preserve the conclusion in an appropriate durable owner before
closing its source.

## What clients can rely on

Streaming text and operational events are provisional. Committed session items
provide replay; display projections choose what the UI shows. An image supplied
for vision is not automatically a displayed image. A persisted artifact requests
presentation, but does not prove that someone saw it.

The same distinction applies to scheduling: a task, a notification and a human
acknowledgement are different facts. Neither silence nor delivery establishes
consent or task completion.

## What survives a change?

- **Client refresh or disconnect:** a client can recover retained history from
  the server. Local unsaved drafts and media state have their own client policy.
- **Worker interruption:** the canonical session remains; supervision and context
  preparation provide recovery mechanisms. Arbitrary external tool side effects
  still require inspection before retrying.
- **Managed frontend publication:** the UI release changes, not the conversation’s
  owner. Preview/publish/rollback is independent of backend replacement.
- **Supervised server replacement:** the application reconstructs resources from
  declarations and durable data. The starter’s parent retains its browser and
  listener. An active voice call needs explicit cleanup/recovery, not an assumption
  that its temporary connection survived.
- **Data deletion or incompatible changes:** persistence alone cannot recover
  deleted files, reverse migrations or undo external effects. Keep backups and
  test recovery for the deployment you operate.

Atomic snapshot replacement protects individual owners from partial writes; it
is not a transaction across all plugin stores or external systems. No framework
mechanism can make every arbitrary command exactly-once.

## Keep mechanisms and policy separate

The Framework supplies storage, supervision, projections and release mechanisms.
The application decides permissions, retention, backup strategy, acceptable
restart boundaries and how to verify a generated change before activation.
A build is not activation; activation is not behavioral verification.

**Next:** [Your first application](../guides/first-application.md).
For exact contracts: [Sessions](../reference/sessions.md),
[Context providers](../reference/providers.md), [Artifacts](../reference/artifacts.md)
and [Deployment](../deployment.md).
