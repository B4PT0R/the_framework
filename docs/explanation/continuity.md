# Continuity and failure boundaries

Continuity means reconstructing work from committed records after temporary
execution changes. It does not mean that every observation survives, a summary
is lossless, or an interrupted external action is safe to repeat.

## What survives depends on its owner

| State | Owner and recovery |
| --- | --- |
| Conversation, configuration, extension state, bindings, command receipts | File-backed `Session`; reopen the canonical snapshot |
| Memory, registry, notes, scheduler records and other feature data | Their feature-owned stores; back them up separately |
| Active model history and compaction anchors | `Session`; compaction replaces active history and bounds the retained archive |
| Browser observation, provider sample, streamed token deltas, sockets/processes | Temporary execution; reconstruct or explicitly preserve useful conclusions |
| Client draft or device permissions | Client-specific policy; not implied by session persistence |

`Session()` is memory-only. `Session.open(path)` enables snapshots, while
`retain_history` controls conversational retention across worker tasks. Durable
settings do not imply retained conversation when that policy is false.

## After an interruption

A worker acquires the session writer lease before opening its record. Startup
sanitizes unfinished tool sequences and recovers command receipts. An incomplete
tool call receives a recorded failure explaining that its external side effects
are unknown. Ordinary queued/active/waiting commands become interrupted; deferred
work with saved delivery information remains waiting for its owning mechanism.
This is protocol repair, not automatic success or an instruction to repeat tools.

A file may already have been written, a process may still be running, or a
remote request may have been accepted before its result reached canonical
history. Inspect actual state before retrying. Command ids and receipts help
correlate delivery; they do not make arbitrary external operations exactly-once.
Atomic snapshot replacement protects a store from partial JSON writes, not from
inconsistency with other stores or external systems.

## Model context is not the full conversation

Clients retrieve a retained archive projection. The text model receives active
history, instructions and fresh provider output. A disappearing provider view
is not evidence that it was never observed: its output was deliberately ephemeral.
Before closing a temporary source, preserve the decisions, facts and uncertainty
needed later in canonical conversation or the relevant durable feature store.

Compaction asks the backend to replace working input with a shorter response.
It does not sample live providers, and a summary cannot be assumed to preserve
every detail. The archive retains earlier material only within the configured
anchor limit; it is **not an unlimited audit log or backup**. See
[Context](../reference/context.md) for exact preparation, request and trimming
rules and a deterministic persistence example.

## Change and recovery matrix

| Change | Retained basis | Limits to verify |
| --- | --- | --- |
| Client refresh/reconnect | Canonical archive, current server state | Local drafts, reconnect policy, device permissions; provisional chunks are not committed messages |
| Managed UI publication/rollback | Same backend/session; previous UI release | Candidate behavior, client compatibility and refresh timing |
| Worker replacement | Saved session and feature data | Interrupted commands, unknown tool effects, in-memory pending compaction flags |
| Supervised server replacement | Private stores and declared composition | Host continuation protocol, cleanup/reconnection, schema compatibility and startup health |
| Context compaction | New active history plus bounded archive | Summary omissions, archive retention and backend failure |
| Voice reconnection | Canonical contributions already committed | Temporary media transport, pending delegation and fresh context reconciliation |

The local desktop host can retain the listener and browser while replacing its
server child. Other deployment modes must supply their own corresponding host
contract. Neither rebuilding source nor building a candidate activates it.
Publication, worker restart and backend replacement are distinct operations.

## Prepare a recoverable update

Keep private data outside replaceable code/releases. Inventory session and
feature stores, back them up together at an appropriate application boundary,
and test restoration. Before a backend/storage change, verify the candidate can
open the intended data or provide an explicit migration/recovery plan. A valid
build alone does not prove compatibility or continuity.

For application self-modification, preserve the canonical session and pending
work, verify the changed behavior, then activate through the host's supported
mechanism. Changes to interface or backend do not expand authorization: external
publication, destructive migrations and external effects still need appropriate
permission.

**Contracts:** [Sessions](../reference/sessions.md),
[Hosting](../reference/hosting.md), [Client surfaces](../reference/surfaces.md),
[Deployment](../deployment.md).
