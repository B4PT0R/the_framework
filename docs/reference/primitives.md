# Built-in application primitives

[Documentation](../README.md) / [Reference](README.md)

Bundled plugins provide capabilities such as memory, file presentation,
command execution and scheduling. Shared storage and lifecycle helpers support
features that need their own state.

This page points to those implementations and their extension points so you can
reuse the existing mechanism when adding application behavior.

## On this page

- [Built-in application primitives](#built-in-application-primitives)
- [Memory and consultation](#memory-and-consultation)
- [Browser and live delivery](#browser-and-live-delivery)
- [Custom hosted runtimes](#custom-hosted-runtimes)

## Built-in application primitives

Reusable agent plugins live in `the_framework.plugins`, separate from the product's
`the_harness.plugins`: `artifacts`, `bash`, `system`, `registry`, `scheduler`, and
`web_search`. Import each capability directly from its subpackage; importing
one does not load the other plugins or product services. Their instruction
files ship alongside their implementations. Application-specific defaults
(including the shell working directory) belong in application configuration.

## Memory and consultation

`the_framework.plugins.memory` owns storage, retrieval, curation tools and orchestration;
applications specialize `memory.agent.MemoryExtension.curator_instructions`.
`memory.search` ranks individual entries; `memory.ask(question)` returns a
read-only specialist synthesis over a complete active-memory snapshot, with
supporting memory IDs/keys and explicit uncertainty. The quiet consultation
profile has no tools, no retained history, and no curation checklist. It shares
the configured memory model/reasoning settings, not mutation capabilities.
Applications may specialize `MemoryExtension.consultation_instructions`.
The session-scoped memory bridge correlates fleet results to the inline tool
call; failures/timeouts are errors rather than invented answers. A session fleet
result handler may return exactly `True` to consume a result delivered inline;
other results retain ordered canonical delivery and all are acknowledged. This
avoids queueing an inline result behind the turn that is awaiting it. Consultation
does not write memories, embed entries, or substitute for the canonical session.
Curator evidence tasks require an atomic candidate inventory with canonical source
IDs. Maintenance without new evidence starts with `inventory([], [], [])`, rewrites
existing entries with empty source/candidate lists to inherit their provenance,
and repeats the empty inventory after a final review before `complete_curation`.
This path cannot introduce candidates or add unsourced memories; completion still
requires every entry within its editorial threshold and total volume below capacity.
## Browser and live delivery

`the_framework.plugins.browser` accepts an explicit runtime directory and uses
`the_framework.server.clients.browser`/`the_framework.server.clients.browser_rpc` for the persistent Playwright process.
`the_framework.plugins.realtime` provides canonical voice projection with application-owned
provider selection. `the_framework.plugins.realtime.controller.RealtimeController` owns transport and
delegation; plugins contribute independent context producers through factories.
Those producers own their state and must stop and await their tasks in `close`.
Use `await controller.append_context(text)` to deliver live-only context without
adding it to canonical history. `delivery="ordered"` preserves accepted updates;
`delivery="latest", key="source"` replaces queued state for that source;
`delivery="discard"` rejects an update while delivery is busy. The controller
waits for peer readiness, active responses and delegated speech. Its bounded
queue returns `False` when full or when no call is active; `True` means delivered
or queued for this call. `discard_context(key)` invalidates queued state, and
`context_blocked` lets producers postpone expensive retrieval. Closing a call
clears queued updates. These public operations replace private transport calls
and application-owned delivery buffers.
Applications retain their own prompts, defaults and specialized context producers.
No product import is required by these shared mechanisms. Runtime observers use
`register_observer`/`unregister_observer`.

## Custom hosted runtimes

Use `Application.build(..., session_factory=...)` when a session needs a
specialized `SessionRuntime`, as the Harness does. The factory receives its worker
supervisor and build context; return a new controller for every invocation. The
builder still owns the canonical APIs, transport and lifecycle wiring. Do not add
a second runtime service manually. For a custom worker executable, the advanced
`supervisor` input accepts a `WorkerSupervisor(command=...)`; custom commands cannot
be combined with its declaration-derived application/agent inputs.
The default fleet uses the same application reference to build private agents;
no forwarding worker command or fleet factory is needed. Custom fleet factories
remain explicit session-runtime overrides.
The default generic worker persists history, configuration, plugin state and
bindings in its canonical session. Static defaults belong in
`AgentSpec.configuration`; optionally declare a
`resources(session_root) -> AgentResources` factory for process-local configuration,
state and optional inference client. It runs during construction, never during
compilation. Explicit `build_agent(resources=...)` replaces this factory. Saved
session values override initial defaults. Updates commit atomically before live
configuration or state changes; a failed write leaves the live values intact.

`the_framework.utils.persistence.MappingStore` remains useful for separate
application service documents. It is not an alternative agent settings store.

The public `the_framework.server` package exports reusable pieces for common agent apps:

- `CanonicalAgentApi` for status, session pages, configuration, compaction,
  interruption and application-defined atomic reset;
- `ApplicationHealthApi` for aggregate and per-service lifecycle health;
- `CanonicalTransportSockets` for ordered agent events and the application
  capability bridge. `build()` exposes it as the
  `canonical_transport` service. Its `event_connection_count` counts accepted
  canonical event sockets, separately from internal stream consumers, the
  application bridge and paired remote connections. Connectivity does not imply
  window visibility or user attention; product presence policy belongs to the app;
- `Application`, `ServerExtension`, `Plugin`, `WebSocketEndpoint` and
  `Application.build()` for composition.

---

[Reference index](README.md) · [Documentation paths](../README.md)
