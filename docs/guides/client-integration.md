# Extend the starter client without becoming a history writer

The starter UI is ordinary editable React code. The server/worker remain owners
of authentication, canonical commands and history. Start from a working
[first application](first-application.md); this guide does not create another
server or ask the browser to call the inference backend.

## Add a feature request

After installing the [Notebook feature](first-plugin.md), use the existing API
helper rather than copying its authentication/error handling:

```javascript
import { api } from "./api.js";

export async function saveProjectNote(project, key, title, body) {
  return api("notebook/notes", {
    method: "PUT",
    body: {project, key, title, body},
  });
}

export async function recentProjectNotes(project) {
  return api(`notebook/notes?project=${encodeURIComponent(project)}&limit=10`);
}
```

The backend validates the same models for worker bridge calls and HTTP. This
client saves directly in the Notebook database; it does not add a fictitious
assistant message or trigger inference. Use chat submission when the intention
is an agent turn. The API helper uses same-origin credentials, handles JSON versus
FormData and sends the tab's `X-Client-Instance`; it is not a secret store.

This is a UI integration fragment, not a complete component. The tutorial's ASGI
and two-worker tests verify the underlying Notebook contracts. A new visual
component still needs UI tests and browser inspection.

## Follow the actual starter state flow

| File | Responsibility |
| --- | --- |
| `src/api.js` | Fetch wrapper and public API errors |
| `src/main.jsx` | Socket ownership, reconnection, status/history reconciliation, submission |
| `src/session.js` | Canonical/provisional display merge, busy state, provider and child activity |
| `src/artifact.jsx` | Supported artifact rendering; no arbitrary executable tool output |
| `src/client-output.js` | Explicit client-instance/media routing and delivery acknowledgements |
| `src/voice.js`, `src/realtime-transcript.js` | Local media lifecycle and transient voice captions |

All paths above are relative to [`starter/ui`](../../starter/ui/). The shipped UI
addresses the default session. A custom session picker must route **both** HTTP
and sockets to the selected session, clear transient state and reconcile the new
history/status; changing only a label does not change worker ownership. See
[Sessions](../reference/sessions.md) for session-scoped prefixes.

## Maintain the commit boundary

1. Open the authenticated event socket and fetch history/status. Keep item ids.
2. Treat `agent.text.delta` as provisional, channel-specific text. Ignore stale
   sequences within the same generation/item; don't concatenate reasoning into
   the public answer.
3. On `agent.response_item.added`, replace the provisional item with committed
   content. Don't append a second copy.
4. Re-fetch pages/status after reconnect and conversational completion. Use the
   server's `next_before`, not a client-generated timestamp cursor.
5. On socket loss, clear provider presentations, captions and child activity.
   Reconnect is not event replay and does not itself prove a server restart.

The starter reducers show one concrete implementation, not a promise that every
custom renderer automatically handles multipart text or all content types. Extend
and test the renderer for the data you choose to expose. Keep provider artifacts
separate: they replace a current view, not historical messages. Specialist events
are child activity; their durable result reaches the caller through the worker.

## Files, media and privileged operations

Use FormData with repeated `files` and let the browser set its multipart boundary;
do not manually set `Content-Type`. Respect server limits and the active-voice
attachment rejection. Files are references to shared bucket storage, not a reason
to serve arbitrary paths. An Image is model vision input; an Artifact is a request
to present content. Neither proves rendering, playback or human attention.

Voice requires explicit user intent, browser microphone permission and a ready
media peer. Stop local media on transport loss; do not silently open a different
device. Preserve only the recovery intent/call identity designed by the voice
client, never credentials/audio in browser storage. Normal clients do not acquire
the desktop `/api/v1/application` bridge. Preview UIs cannot write production
configuration or send prompts.

## Verify and activate deliberately

Follow the starter's [UI development commands](../../starter/README.md), run the
relevant reducer/component tests and build. In applications exposing surface
controls, build a candidate, inspect its authenticated read-only preview, then
publish only when authorized and verified. A build/source edit is not activation;
a published UI is not a server restart. Do not fake prompt execution in a
read-only preview. [Surfaces](../reference/surfaces.md) defines that lifecycle.

For a completely different client, implement the [transport](../reference/conversation.md),
[typed event](../reference/events.md), [artifact](../reference/artifacts.md) and
[security](../reference/security.md) contracts explicitly rather than copying the
starter's local credentials or assuming unlimited event replay.
