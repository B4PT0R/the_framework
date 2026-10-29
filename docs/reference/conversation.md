# Conversation HTTP and WebSocket transports

The server accepts commands; one worker writes each session. HTTP acceptance,
worker completion, canonical persistence and client display are different stages.
A successful submission is not a model answer or proof that the user saw it.

Applications with declared agents receive the host-owned `chat`, `runtime` and
`canonical_transport` services. They are not optional agent plugins. Service-only
applications have no canonical conversation API. Paths below address the default
session; [independent sessions](sessions.md#host-multiple-independent-conversations) describe
explicit session routing.

## Submit text or attachments

| Request | Input | Successful response |
| --- | --- | --- |
| `POST /api/v1/agent/prompt` | JSON `{id, prompt}`; nonempty strings, id at most 200 characters | HTTP 202 `{id, status: "submitted"}` |
| `POST /api/v1/agent/attachments` | Multipart `id`, 1–32 repeated `files`, optional `prompt` | Same acceptance envelope |
| `POST /api/v1/agent/interrupt` | JSON with optional nullable `reason` | HTTP 202; interruption is queued, not confirmed |
| `POST /api/v1/agent/compact` | No inputs | HTTP 202; compaction is sequential worker work |

Generate a command id on the client, keep it when correlating progress, and do not
interpret a lost response as proof that submission failed. Attachment copying may
already have succeeded and worker acceptance may already have occurred. Chat HTTP
does not implement an application-level transactional receipt store across upload
and worker execution. Repeated file submissions are not an exactly-once upload
protocol.

An authenticated same-origin browser can submit this JavaScript after the hosting
application has provisioned its credentials:

```javascript
const id = crypto.randomUUID();
const response = await fetch("/api/v1/agent/prompt", {
  method: "POST",
  credentials: "same-origin",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({id, prompt: "Summarize the current project."}),
});
const receipt = await response.json();
if (!response.ok) throw new Error(receipt.error?.message || "Submission failed");
// Keep receipt.id for correlation; read events/history for the actual result.
```

The starter additionally sends `X-Client-Instance` and connects its event socket
with the matching `client_instance` query parameter. This tab identity routes
explicit client media operations; it is **not** authentication. Reuse
[`starter/ui/src/api.js`](../../starter/ui/src/api.js) and
[`client-output.js`](../../starter/ui/src/client-output.js) when extending that UI.

Uploads are stored in the shared `BuildContext.data_root / "bucket"` tree, not
inside the browser or one session's JSON. Chat creates attachment/copy-reference
messages and vision inputs for supported decoded image files. It does not parse
PDFs or Office documents. The user-facing file notice and developer path reference
are ordered inputs to the worker. Attachments are rejected with `409 voice_active`
while a call is active; stop voice first.

### Upload limits and failure

`Application.max_upload_bytes` defaults to 64 MiB **combined file contents** per
request. Set a positive count or explicitly `None` to disable byte limits.
Declarative multipart parsing also bounds the actual incoming stream to that
limit plus 1 MiB of fields/filenames/framing, regardless of `Content-Length`.
Crossing this envelope returns `413 upload_too_large`; file contents over the
copy limit return chat's `422 invalid_attachment`. There are at most 32 files;
parser field limits still apply with byte limits disabled.

Parsed uploads close even after validation errors. Failed copies remove partial
files; successful copies never overwrite existing paths. Invalid supported image
files are rejected and that copy batch is removed. These are not server-wide body
limits: JSON and raw application routes need their own policy. File storage,
publication and browser rendering are described under [Artifacts](artifacts.md).

## Read and reconcile

| Request | Use |
| --- | --- |
| `GET /api/v1/session?limit=25&before=…` | Display-projected page of archived turns, `has_more` and `next_before`; limit 1–100 |
| `GET /api/v1/status` | Worker/queue, context estimates, cache/quota measurements and server identity |
| `GET /api/v1/config`, `PATCH /api/v1/config` | Canonical configuration; patch waits for the worker's applied result |
| `GET /api/v1/state` | Plugin state projections, not arbitrary process memory |
| `DELETE /api/v1/session` | Application-owned reset; returns 501 when no reset policy is installed |

Read [Sessions](sessions.md) for page/watermark semantics, [Context](context.md)
for estimates, and [Extensions](extensions.md) for config/state schemas. One
status timeout has a historical special response: HTTP 503 with a string `error`,
not the ordinary structured error object. Clients should tolerate that explicitly.

### Canonical event socket

`/api/v1/events` sends JSON **worker output envelopes**, not bare events:

```json
{"type":"command_event","request_id":"client-id","command_id":"client-id","event":{"type":"agent.text.delta","item_id":"reply-id","kind":"message","channel":"output","part_index":0,"delta":"Hello","sequence":0}}
```

Other envelopes include `command_accepted`, `command_completed`, `command_failed`,
`worker_status` and `subagent_event`. The typed vocabulary and commit boundary are
in [Events](events.md). Read the command type before interpreting completion: a
config command is not a completed conversational reply.

The canonical socket is a live subscription, **not a replay log**. A runtime
subscriber queue holds 512 outputs; overflow removes the subscriber and closes
its stream rather than silently delivering an incomplete live feed. The transport
accepts one primary event connection per session; a second primary is rejected
with 1013. Read-only preview connections are distinct. This limit does not prohibit
independent sessions or the separate paired-client transport.

On connection/reconnection, fetch history and status, merge by item id, discard
transient provider/specialist/caption state and reconstruct the current UI. Do not
append a delta after a canonical item has replaced its provisional text. The
starter opens the socket before fetching snapshots and reconciles again after
prompt completion. It implements exponential reconnect backoff, not event replay.
See the [client integration guide](../guides/client-integration.md).

`/api/v1/application` is the exclusive desktop capability bridge, not the chat
stream. It accepts only a primary role and executes application-owned operations;
an ordinary web/mobile client must not claim that bridge. [Hosting](hosting.md)
describes its correlation, cancellation and execution boundary.

## Reuse ingress from another transport

Inject the session-scoped `chat` service and call
`await chat.ingress.submit(PromptRequest(...))`. `ChatIngress` is also importable
from `the_framework.server.runtime.ingress` for explicit non-HTTP assembly. Share
one instance across transports, with the installed voice service or a shared
`text_lock`. Authentication, receipt persistence and wire errors remain the
transport owner's responsibility.

Ingress selects text or active voice under the **same lock** as voice transitions.
It returns `{id, route: "text" | "realtime"}`; voice returns the voice turn id,
whereas the local HTTP endpoint preserves its submitted client id. Voice accepts
ordinary user text only: structured input, suppressed prompts, custom role/kind,
response kind or per-command reasoning overrides raise `ValueError` rather than
silently losing semantics. HTTP maps voice submission failure to 409.

For text commands, `PromptRequest.response_kind` labels generated messages and
text events for that turn, including steering; it is restored after completion
or failure and never sent as an extra model field. Realtime delegates use
`realtime_delegation`; canonical spoken transcripts use `realtime`.

## Planned restart signals

`POST /api/v1/server/prepare-restart` requires the `server:write` authorization
requirement. It prepares every open session before transport shutdown: service
restart hooks run, then the worker barrier drains conversation work. A supervisor
still owns actual process replacement; preparing is not restarting.

Clients receive transient `server_restarting` with `instance_id`/`phase`, or
`server_restart_failed` with an error if preparation/restart fails. Status exposes
`server: {instance_id, phase}` after the worker answers. Compare process identities:
a socket reconnect alone does not prove a new server. Voice has additional
post-drain signals and explicit client-owned recovery; see [Realtime](realtime.md).

## Source and verification

- `server/api/{chat,agent,transport,uploads}.py`, `server/runtime/ingress.py`:
  acceptance, file handling and locking.
- `server/runtime/session.py`: live queue, overflow and command correlation.
- `tests/general/test_chat_ingress.py`, `test_uploads.py`,
  `test_canonical_transport_presence.py`, `test_server_restart_status.py`.
- The starter's `main.jsx`/`session.js` are actual display/reconnect consumers,
  not a second owner of canonical history.
