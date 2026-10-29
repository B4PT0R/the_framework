# Chat ingress and live-context integration

[Documentation](../README.md) / [Reference](README.md)

Use this for text/file submission, voice transitions, restart barriers and live-context contribution contracts. For an overview, read [A conversation through the system](../explanation/architecture.md#a-conversation-through-the-system).

## Canonical chat and optional live context

For an application declaring agents, `Application.build()` installs the host-owned `chat` service,
including `POST /api/v1/agent/prompt` and `POST /api/v1/agent/attachments`.
It is part of the conversation assembly, absent from the plugin catalogue, and
cannot be disabled through plugin controls. A client can omit the chat UI while
keeping these APIs available. An application without declared agents
has no conversation services.
Both accept a client-generated command `id`; attachments use multipart form data
with `files` and an optional `prompt`. The service coordinates with the installed
Realtime API's lock, so attachment acceptance and voice startup cannot race.
`BuildContext.data_root` owns storage; uploads use its shared `bucket/` tree.
No voice plugin is required for text-only apps.

`Application.max_upload_bytes` defaults to 64 MiB of combined attachment contents
per request. The composed chat service passes this limit to its transactional copy;
`copy_uploaded_files(..., max_total_bytes=...)` uses the same default independently.
All declarative multipart endpoints also bound their actual incoming stream to this
limit plus 1 MiB for fields, filenames and framing, before feeding it to the parser.
They do not trust `Content-Length`; a crossing chunk is rejected with HTTP 413
(`upload_too_large`) and partial spools are closed. A request within the envelope
whose files exceed the content limit is rejected by chat with HTTP 422. There are
at most 32 files per multipart request. Parsed uploads are closed after dispatch,
including schema errors; copying removes partial files and never overwrites existing
paths. Set a positive byte count to change the limit, or explicitly `None` to opt out
of the byte limits (the file-count and parser field limits still apply). These limits
are not a server-wide body policy: raw plugin routes and JSON requests are unaffected.

Other client transports inject the host-owned `chat` service into their factory,
then reuse `chat.ingress.submit(PromptRequest(...))`. Do not inspect voice state
and submit directly: selection and acceptance must hold the same lock as voice
startup, shutdown and binding changes. The operation returns `{"id": ..., "route":
"text" | "realtime"}`; in voice mode the id is the voice turn id. HTTP contracts,
authorization and per-client idempotent receipts remain owned by the caller.
The local prompt endpoint still returns its client-generated id.

`ChatIngress` is also available from `the_framework.server.runtime.ingress` for
non-HTTP applications. Construct it with a runtime and, optionally, the installed
realtime service (controller and transition lock). Without voice it uses an
optional `text_lock`, or its own lock. Share one ingress between transports.
`PromptRequest.response_kind` selects the canonical kind of generated assistant
messages and their streamed text events for that command. It is scoped to the
turn (including steering), restored on completion or failure and absent from
model API input. Realtime delegation uses `realtime_delegation`; spoken canonical
transcripts retain `realtime`, allowing clients to distinguish both on replay.

Successful voice-call creation also queues a canonical developer message of kind
`realtime_mode`, announcing the start of realtime interaction. The same message
is included in the voice model's initial context, reserving one initial-item
slot. Shutdown and transport cleanup queue one matching end marker. Both carry
a stable `voice_session_id` and `voice_state`; repeated delivery is idempotent.
These markers remain hidden from the display projection, but are included in
text context and later voice projections. Failed call creation emits no markers.

`ExternalEventRequest.input_items` permits ordered canonical input without
starting inference. Items are committed by the worker before dispatching the
external event, even when its plugin binding has been removed. Realtime mode
markers use this mechanism so disabling voice cannot discard an accepted end
marker. Shutdown drains the worker only after final transport cleanup and marker
submission; tool-initiated shutdown may queue these writes without waiting for
its own command to finish.

Supervised server restarts prepare every open session before closing HTTP or
WebSocket transports. Session services register `register_restart_hook()` callbacks;
Realtime pauses input, finishes the current spoken turn, closes the call and drains
canonical writes. A worker barrier then finishes pending conversation work. Local
supervisors can POST `/api/v1/server/prepare-restart` (scope `server:write`) before
replacing the server. The system plugin chooses recovery from the actual active client call at the
completed-turn boundary, not the originating command type. It reconnects that
work only through a ready call presenting the previous owner id. Agentic
continuations wait during recovery; after 45 seconds without a bound delegation,
the canonical text agent resumes instead. This fallback never opens a microphone.

The starter's browser retains voice intent and the previous client call id in
tab-local `sessionStorage`, never credentials or audio. WebSocket loss releases
local media without clearing that intent. After reconnection, idle canonical
status permits exactly one new call; explicit stop cancels recovery. Interface
publication first POSTs `/api/v1/realtime/calls/current/suspend` with the owning
call id, finishing and persisting its turn before reload. The replacement UI uses
the same recovery path. A changed or unrelated call id cannot stop another client.

Voice accepts ordinary user prompts only: input items, suppressed prompts,
custom roles/kinds and per-command reasoning overrides raise `ValueError` rather
than silently losing text-only semantics. Voice submission failures also raise
`ValueError`; callers choose their transport error representation. Cancellation
and failures release the barrier. Attachment storage continues to hold this same
barrier until its ordered text command is accepted.

A feature that produces live context can contribute a factory through
`the_framework.plugins.realtime.context.CONTEXT_FACTORY` in its server assembly.
The factory receives the voice controller. Contexts implement `start(projection)`
synchronously and optionally `ready()`, `flush()`, `handle_worker_output(output)`
and `close()`; those optional hooks may be synchronous or awaitable. The controller
calls them at its existing transport barriers. Each producer owns its state and
must cancel its tasks in `close`; every producer gets cleanup even when another
producer fails. Recovery prefixes may be declared as `recoverable_context_prefixes`.
The memory and registry plugins are independent examples of this seam.
A producer may declare `provider_name` to run only when that provider is selected
in the voice projection's `providers` list. Selection combines the agent binding,
realtime channel and application whitelist. Installed producers with no selected
provider receive no delivery hooks; all producers still receive cleanup.

A plugin is a complete vertical application feature, rather than a tool palette.
Installing or removing one changes startup composition and requires a server
restart; changing its agent binding leaves its installed services and data intact.
These changes are expected to be infrequent. While voice is active, a binding
change closes the ephemeral call and clears pending context and producer tasks.
The canonical worker owns transcription commits independently of the realtime
binding: a final accepted transcript still persists if voice is disabled before
its queued execution. It applies the binding and drains final voice turns before the
server publishes `realtime_reconnect_requested` with the owning browser's client
`call_id`. The starter and Harness renegotiate from a fresh canonical snapshot.
Reconciliation runs outside the binding operation, so an agent tool can request
its own binding change without waiting for itself to finish.

A manual stop cancels the client's intent to reconnect. Stale or foreign call IDs
are ignored, and disabling the realtime binding ends voice without restarting it.
Failed reconciliation reports an error rather than opening a call from uncertain
history. Server shutdown cancels pending reconnect tasks. This is a full call
replacement; no claim is made that an existing model can unsee provider context.
The reconnect signal is transient control data, never a conversation item.


Keep this seam narrow: it supplies context, not another event bus or task runtime.
Applications do not need a new controller subclass just to combine these plugins.


Response waits use the same scheduler persistence, lock and worker queue.
`create_wait(wait_json)` records a nonblocking reply/ACK deadline; `list_waits`,
`get_wait` and `resolve_wait` inspect or resolve it. Optional alert occurrences
start their ACK deadline at the alert time. A human prompt resolves at most one
next-reply wait without an additional agent turn; native ACK/timeout dispatches a
stable queue command. Silence is never consent or task completion. Device receipts
and late responses remain inspectable. There is no semantic reply classifier or
automatic urgency policy. The plugin instructions document the full contract.

`SchedulerService.native_alerts()` is a read-only seven-day projection of known
next wake occurrences, active task reminders and explicit ACK alerts. It never
moves recurrence to clients or changes task/wake state. Truncation is explicit;
clients must not replace a complete local schedule with a partial snapshot.
Delivery adapters authenticate device receipts and call `wait_call("receipt", ...)`;
the Framework does not select a mobile push vendor or assume client connectivity.

---

[Reference index](README.md) · [Documentation paths](../README.md)
