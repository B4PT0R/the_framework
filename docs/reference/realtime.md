# Realtime voice: projection, calls and continuity

Install `the_framework.plugins.realtime.REALTIME` to add a session-scoped voice
service and its agent extension. It is a temporary live model connection around
the canonical text worker, not a second history writer. Clients own microphone
permission/audio media; the server owns call transitions/delegation; the worker
commits accepted transcripts. A connected peer or generated audio does not prove
human receipt.

## Inspect the projection without a call

This complete example exercises the real snapshot projection, reserving one slot
for the controller's current-call start marker. It makes no backend request and
opens no microphone:

```python
from the_framework.agent import Agent
from the_framework.plugins.realtime import RealtimeExtension
from the_framework.plugins.realtime.mode import voice_mode_marker

agent = Agent(client=object())
voice = agent.add_extension(RealtimeExtension)
voice.config.max_initial_items = 2
agent.add_message("user", "What is the project status?")
agent.add_message("assistant", "The tests passed; publication is still pending.")
projection = voice.session_snapshot({
    "items": list(agent.session.history), "extensions": {},
})["extensions"]["realtime"]
initial = projection["session"]["initial_items"]
assert len(initial) == 1
assert initial[0]["role"] == "assistant"
assert projection["protocol"] == "codex-realtime-v3"
assert projection["providers"] == []  # explicit whitelist, not every provider
marker = voice_mode_marker("transport-call-1", "started")
assert marker.kind == "realtime_mode" and marker.role == "developer"
assert len(agent.session.history) == 2  # projection/marker creation wrote nothing
```

Installing only `RealtimeExtension` on a standalone Agent does **not** install the
HTTP/controller/media service. Use the complete `REALTIME` plugin in a hosted
Application. Conversely, a disabled agent binding does not uninstall its server
routes. Fake-client tests verify protocol behavior, not a live backend/account.

## Configuration and selection

`RealtimeConfig` currently declares model `gpt-live-1-codex`, voice `maple`,
`max_initial_items=128` (allowed 2–128), `delegation_ack_filler=True`,
`provider_whitelist=[]`, `provider_slot_max_characters=12000` and
`instruction_max_tokens=16000`. These are checkout defaults, not promises that
an account supports a particular model/voice. `realtime.client_factory` is an
optional server-side plugin option for the backend client factory.

Snapshot projection renders `vocal` instructions and text from conversational
user/assistant messages, mode markers and artifact references. It does not feed
opaque Responses compaction items to the live call. Initial items keep the newest
configured window minus the reserved current marker slot; they are **not the
entire archive**. Ordinary text model projection is separate.

A provider must be bound/available, advertise the `realtime` channel and be named
in the explicit whitelist. Provider slots are bounded by characters, then fitted
within the instruction-token budget; canonical vocal instructions exceeding the
budget raise rather than being silently dropped. Provider context is ephemeral,
not additional canonical user speech. See [Providers](providers.md).

## Client and tool lifecycle

| Operation | Contract |
| --- | --- |
| `POST /api/v1/realtime/calls` | `{call_id, offer_sdp, resume_call_id?}`; requires idle foreground text work, active binding and no existing call; returns negotiated call data |
| `POST /api/v1/realtime/calls/current/ready` | `{call_id}`; matching owning client reports connected media before context delivery |
| `DELETE /api/v1/realtime/calls/current?call_id=…` | Graceful stop, HTTP 204; a foreign/stale id does not stop the current call |
| `POST /api/v1/realtime/calls/current/suspend` | Owning `{call_id}`; drain/reconcile before UI reload/recovery |
| Agent `session_status` | Actual call/pending control status |
| Agent `start_session`, `stop_session` | Deferred control request, not confirmation of media startup/shutdown |

HTTP call operations declare `realtime:write`; authorization remains the
application's security policy. The browser's client call id and backend transport
identity have distinct roles; do not substitute one for the other.

Tool start/stop execution waits until the originating agent command finishes.
Start targets only the primary interface, never a preview or a silent fallback
device, and browser microphone permissions still apply. Stop lets the current
spoken response, including delegated results, finish without waiting on the very
tool result it needs. Failures publish control errors rather than claim success.

Text submission goes through the shared [ingress barrier](conversation.md#reuse-ingress-from-another-transport).
While voice is active, ordinary typed text routes to voice; custom structured text
semantics and attachments require text mode. Delegation submits canonical worker
work using `response_kind="realtime_delegation"` and low reasoning effort; the
voice adapter consumes Framework events and does not speak reasoning channels.

## Canonical transcripts and mode markers

The voice adapter normalizes `voice.transcript.updated` into cumulative transient
captions. `voice.turn.completed` carries role, turn identity, final transcript and
interruption; final captions clear. `voice.response.state`, `voice.speech.state`
and `voice.error` are typed feature events inside `RealtimeEvent`, not backend
wire passthrough. Only worker commits create canonical transcript messages.

Successful call creation queues a developer `realtime_mode` start message, also
included in the new live context. Cleanup queues its matching end marker. Stable
`voice_session_id`/`voice_state` and item ids make repeated marker delivery
idempotent; failed call creation adds no markers. Display projection hides them,
while text context and later voice snapshots retain their meaning.

`ExternalEventRequest.input_items` commits ordered input before feature-event
dispatch, even after the corresponding plugin binding is removed. Thus an accepted
end marker/final transcript is not lost just because voice was disabled before
queued processing. Shutdown drains final transport cleanup/marker writes before
closing the worker. Tool-initiated shutdown can queue those writes without waiting
for its own command to finish.

## Live-context contributions

A feature's configure hook can contribute a controller factory under
`the_framework.plugins.realtime.context.CONTEXT_FACTORY`. Each resulting producer
owns its state/tasks and implements synchronous `start(projection)`; optional
`ready()`, `flush()`, `handle_worker_output(output)` and `close()` may be sync or
awaitable. The controller invokes them at its existing transport barriers. Every
installed producer receives cleanup even if another fails.

Optional `provider_name` gates delivery hooks to the selected snapshot provider;
unselected producers still get cleanup. `recoverable_context_prefixes` identifies
feature-owned transient context echoes. Memory and registry use this seam without
requiring a controller subclass. It supplies live context, not another task
scheduler or canonical history store.

For controller-based producers, `append_context(text, key=..., delivery=...)`
supports ordered deltas, keyed `latest` state and `discard` delivery. Latest needs
a key; `discard_context(key)` invalidates queued state. Delivery waits for peer
readiness and response/delegation barriers. Queue pressure can reject updates;
check the returned acceptance rather than claiming delivery. Echo filtering keeps
these context inputs out of canonical user transcripts.

## Binding changes, reload and restart

Changing a binding while voice is active replaces the ephemeral call and clears
its pending contexts/tasks. After applying the worker binding and draining final
voice work, the server publishes `realtime_reconnect_requested` with the owning
browser's call id. Reconciliation is outside the binding operation so a tool can
change its own binding without deadlocking. Disabling realtime ends voice without
restarting it. Failure reports an error instead of negotiating from uncertain
history; shutdown cancels pending reconnect tasks. A call cannot "unsee" old
context: replacement is intentional.

The starter stores only voice intent and previous call identity in tab-local
session storage, never credentials/audio. Socket loss releases local media but
preserves that intent; once transport is back and canonical foreground work idle,
it attempts one new call. Explicit stop clears intent; foreign ids do not control
another client's call. Interface publication first suspends/drains the owning call
before reload, then uses the same recovery path.

Planned server restart runs registered service hooks before HTTP/socket shutdown.
Realtime pauses input, finishes speech, closes/drains the call and canonical writes.
The optional system plugin chooses voice recovery from the actual active call at
the completed-turn boundary, not the command type. Recovery binds only through a
ready call presenting its previous owner; agentic continuations wait. Its default
45-second recovery timeout resumes canonical text work instead; it never opens a
microphone. These guarantees require that supervised recovery integration, not
merely installing the voice extension. See [Continuity](../explanation/continuity.md)
and [Deployment](../deployment.md).

## Source and verification

`plugins/realtime/{agent,api,controller,mode,context,events,live_events,server}.py`
own these contracts; `starter/ui/src/voice.js` owns browser media/recovery intent.
`plugins/system/service.py` owns supervised voice-to-text fallback. Tests in
`tests/general/test_realtime_{context,control,mode,restart}.py` exercise fake-media
barriers, accepted transcript persistence, binding changes, control deferral and
recovery. The projection example is part of the executable documentation tests;
none of those checks proves audible delivery on a real device.
