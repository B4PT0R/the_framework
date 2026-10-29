# Realtime and voice

[Documentation](../README.md) / [Reference](README.md)

Use this for voice controller, projection and transport contracts. Read [Continuity](../explanation/continuity.md) before integrating a new client.

## Realtime service

The neutral realtime plugin exposes call creation/readiness/stop endpoints and
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
