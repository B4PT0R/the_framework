# Realtime and voice

[Documentation](../README.md) / [Reference](README.md)

The Realtime plugin adds a live voice call alongside the text conversation.
Its controller manages call startup, readiness, delegated work and graceful
shutdown; clients supply their microphone permissions and media connection.

The contracts below describe call control. For coordinating voice with text
submissions and feature updates, see [Conversation ingress](conversation.md).

## Realtime service

The Realtime plugin exposes call creation/readiness/stop endpoints and
its controller, with an optional `realtime.client_factory` plugin option.
Its agent tools `start_session`, `stop_session` and `session_status` control the
same call. Start/stop acknowledge a deferred request, not a completed transition:
execution waits for the originating agent command to finish. Start targets only
the primary interface (never previews), subject to browser microphone permission.
Stop drains the current spoken response, including a delegated tool result,
before closing the call; it never blocks the tool result needed for that response.
Status reports the active call and any pending request. Control failures are
published to clients without claiming the requested transition succeeded.

For browser execution and observation, see [Browser](browser.md). For
live-context contributions and transition barriers, see [Conversation ingress](conversation.md).

---

[Reference index](README.md) · [Documentation paths](../README.md)
