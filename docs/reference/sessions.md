# Independent application sessions

[Documentation](../README.md) / [Reference](README.md)

Use this to host separate conversations in one application. A specialist is not a second public application session; see [Agents](agents.md).

## Independent application sessions

`build()` keeps the existing `/api/v1/...` API for the default
session. `Application.agents` optionally declares other selectable agents.
Authenticated consumers can create or reopen sessions with
`POST /api/v1/sessions` (`agent`, optional `session_id`, `durable`, default true),
list them with `GET /api/v1/sessions`, and stop an additional worker with
`DELETE /api/v1/sessions/{id}`. Closing preserves durable session data; the
default session follows the application lifecycle.

The response returns an `api` prefix: `/sessions/{id}/api/v1`. History, settings,
plugin bindings, voice and event/application sockets under this prefix belong to
that session. A request to a known persisted session lazily resumes its worker;
an unknown id never creates a worker implicitly. Persistent sessions live under
`data_root/sessions/{id}/session.json`. Resident sessions disappear on close.
Python hosts use `app.state.application.open_session(...)` and `close_session(id)`.
A filesystem writer lock rejects another worker trying to own the same session.

`ServerExtension.scope="application"` (default) shares one service and lifecycle
across sessions. Its routes remain at the root. `scope="session"` builds an
independent service and routes for each session. The standard conversation,
realtime, scheduler and system services use session scope. Their default private
files are relative to `session_path`; the shared file bucket stays application-wide.
Application-wide desktop/remote bridges keep their application policy; selecting
a session in a custom client requires using that session's API and sockets.
The starter and Harness still show their default conversation.

Every runtime owns its own specialist fleet. `subagent_event` carries `agent`,
`session_id`, `task_id`, caller `command_id` and an activity `event`. This ephemeral
feedback never enters the caller history. Durable results retain their ordered
delivery and acknowledgement contract independently of activity feedback.

---

[Reference index](README.md) · [Documentation paths](../README.md)
