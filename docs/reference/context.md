# Context and shared mechanics

[Documentation](../README.md) / [Reference](README.md)

Use this for ephemeral live context and the boundary between reusable mechanics and feature-owned state.

## Optional live context

The voice controller relays canonical `agent.compaction.start` and
`agent.compaction.end` events as ephemeral operational context, plus a failure
notice if the owning command fails during compaction. These notifications use
`session.context.append` immediately, even while delegation/provider refreshes
are blocked, so the vocal model can explain an ongoing wait. They neither request
speech nor add voice tools, open a call, or write canonical history. A compaction
completion notice is not a task-completion notice.

The voice controller owns transport, delegation barriers and ephemeral-context
reconciliation, not knowledge of memory or registry contents. Installed memory and registry plugins contribute their own live-context factories
through the Realtime contribution contract described in
[Chat ingress and live-context integration](conversation.md). Context producers own their caches, revision tracking and tasks;
the controller owns the ordered, token-bounded delivery path. Close producers
before releasing the transport. The memory producer interprets its own
`relevant_memory` XML from the retained `provider_slots`; Realtime does not parse
memory or expose a separate memory-candidate projection. The memory candidates
therefore reflect the provider slots retained after instruction-budget fitting.
The Harness demonstrates this composition beside
its own domain context; no general provider manager is required.

## Shared mechanics, separate ownership

Application and plugin services use one startup graph and one rollback path.
The graph validates dependencies across ownership boundaries, starts providers
before consumers, and stops everything in reverse order. Agent-binding changes
do not restart services or alter server routes.

`atomic_text_writer` shares private temporary-file creation, replacement, file
and directory synchronization, and cleanup. Session, registry, mapping and fleet
stores keep their own serialization, validation and writer ordering. The primitive
is not a locking or multi-file transaction API; a directory-sync failure after
replacement logs uncertain durability at ERROR, without raising or rolling back.

---

[Reference index](README.md) · [Documentation paths](../README.md)
