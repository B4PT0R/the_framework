# Security and endpoint contracts

[Documentation](../README.md) / [Reference](README.md)

Use this when defining access policies, route schemas or authenticated transports. The starter policy is local-only; it is not a public hosting recipe.

## Security and endpoint contracts

Remote client privileges are application-owned: `RemoteClientPolicy()` grants
no scopes, capabilities, or platform-specific privileges. Supply the policy
explicitly to `ClientRemoteService` (which passes it to its credential store).
Routed and default leased capabilities must be declared exclusive capabilities;
invalid policy graphs fail at construction. An application with several installed
features can combine their `RemoteClientPolicy` contributions with
`RemoteClientPolicy.compose(base, feature, ...)` before creating the service.
Independent features can contribute policies through `BuildContext`; the
remote-client owner's service factory composes them after all runtime
declarations have been resolved.
Duplicate capability or event ownership is rejected, and policy changes are
forbidden after service startup. Plugin runtime factories run in capability
dependency order, while collected contributions do not rely on that order.
Paired-client records are not rewritten by policy composition.

Authenticated endpoints require an explicit application `SecurityPolicy`.
There is no implicit allow-all policy in `Application`. The registry:

1. authenticates the request;
2. evaluates the endpoint's authorization requirement;
3. parses and validates path/query/JSON input;
4. calls the handler, sync or async;
5. validates JSON output;
6. translates controlled failures into a stable error envelope.

Native FastAPI/Starlette `Response` objects pass through unchanged for streams,
range responses and downloads. The low-level `PermitAllSecurity` policy exists
only for explicit tests and compatibility adapters.

JSON is the default request encoding. Multipart endpoints opt in explicitly
with `request_encoding="multipart"`; array properties remain arrays even when
the request contains only one file, and normal JSON Schema cardinality limits
are enforced before the handler runs.

WebSocket handlers use the matching `@websocket` declaration and are composed
through `ServerExtension(websockets=(...))` or service discovery above. They share
the same authentication and authorization policy, reject unauthorized upgrades
with close code `1008`, and are collision-checked before any route is installed.

Protocols that authenticate during their own handshake can construct a
`WebSocketEndpoint(..., handshake=...)`. The handshake returns a small
connection context that is injected into the handler, or `None` to reject the
upgrade. This supports versioned subprotocols, replay cursors and private-client
credentials without weakening the ordinary HTTP security policy.
Server-push event handlers must also read the peer's disconnect signal while
waiting for the next event. The framework's local and remote event streams use
one shared routine for this, so an idle disconnected client releases its
subscription promptly instead of lingering until a heartbeat or shutdown.

Planned server restarts publish `server_restarting` (with `instance_id` and
`phase`) to each live session before draining restart hooks. A preparation or
supervisor failure publishes `server_restart_failed` with its error and restores
the ready phase. `GET /api/v1/status` includes `server: {instance_id, phase}`
after the worker has answered. Clients can confirm a restart by comparing that
process-scoped identity with the announced old one; a reconnected socket alone
is not proof. These are transient lifecycle signals, not conversation messages.
Voice keeps its own post-drain restart notification so the general announcement
does not cut an ongoing spoken turn.

Worker status also includes durable `cache_hit`: `ema_percent`, `last_percent`,
`samples`, `unreported`, `updated_at` (Unix seconds of the last valid sample), and
`alpha` (0.1). Each completed/incomplete Responses generation with valid usage
contributes `100 * input_tokens_details.cached_tokens / input_tokens`; the first
sample seeds the EMA, then `EMA = alpha * sample + (1 - alpha) * EMA`. This is an
average of per-call percentages, not weighted by request size and not a quota
savings estimate. The conventional span is 19 samples (half-life ~6.6 samples).
Absent/invalid usage clears `last_percent` and increments `unreported`, leaving the
EMA, sample count and measurement time unchanged. An unmeasured EMA is `null`,
not zero. The worker owns observation and session persistence; reconnect/status
polling never samples again. Metrics are not inserted into the model prompt.
Each worker/session measures its own generations, not separate subagent sessions,
Realtime voice or the distinct compaction endpoint. Failed/interrupted streams
without a completed/incomplete response are not observations.

---

[Reference index](README.md) · [Documentation paths](../README.md)
