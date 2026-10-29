# Troubleshoot by the failing boundary

Start with the exact command, interpreter, application module, data directory,
expected behavior and error. Separate **a prepared environment**, **a running
server**, **a healthy worker**, **an authenticated client** and **a successful
model request**. Success at one layer does not establish the others.

Do not paste tokens, cookies, model credentials or private session contents into
reports. Preserve data and inspect before retrying an operation with side effects.
For installation steps, see [First application](first-application.md); for test
environments, see [Development](development.md).

## Environment and preparation

| Symptom | Check first | Next action |
| --- | --- | --- |
| Import/command missing or old API | Interpreter and installed package location; checkout versus copied app | Use the intended environment; install the package there, not into an unrelated Python |
| `uv sync` or npm preparation fails | Exact failing subprocess, network/cache, manifest/lockfile and supported Python | Correct that prerequisite, then repeat explicit `the-framework prepare --code-dir ...` |
| Chromium executable/revision missing | Installed Playwright driver, actual browser cache, headless shell | Explicitly prepare that driver's Chromium; do not choose an arbitrary binary |
| Browser launch fails despite executable existing | OS libraries, process permissions and launch error | Repair the identified system prerequisite with permission; do not silently disable sandbox/security controls |
| Test ignores application browser cache | `tests/conftest.py` removes inherited cache environment | Pass pytest `--browser-cache PATH`; see the development guide |
| Bootstrap refuses a directory | Code/data roots must be empty or absent, distinct and non-overlapping | Choose correct new locations; never delete existing user data just to satisfy bootstrap |

Preparation may install dependencies/build a UI; it does not log in to a model
account or prove inference works. A successful npm build does not publish that
candidate. A local missing-runtime skip is not a successful browser check.

## Server, worker and application assembly

| Symptom | Boundary and useful checks |
| --- | --- |
| `Application.current()` unavailable | Only valid during the live lifespan, not import/compile or after shutdown; use factory `context.application` during construction |
| Another application already running | Exactly one live Application per process, including across threads; close the previous lifespan rather than changing a global pointer |
| Missing service/capability or cycle | Inspect `Plugin.requires/provides`, `ServerExtension` factories, DI parameter names and application/session scope; declarations precede service construction |
| App starts but no UI | Check explicit `surfaces`, module-relative `ui/`, compiled artifact and published release; missing initial bundle permits backend repair |
| Worker startup fails | Use its failure/log output and importable application reference; server and worker do not share live Python objects |
| Saved agent name cannot be resolved | The durable session selects its recipe; restore an appropriate declaration or make an explicit data decision, not a silent recipe substitution |
| A service keeps running after a plugin toggle | Expected: installed graph lives until shutdown; binding changes only agent exposure |

Check authenticated health/status for the actual session/runtime. Do not infer
worker readiness from a listening socket alone. Use application-owned lookup
`runtime.service(name)`/documented runtime properties, not an invented global
current-session pointer or an application-scoped `runtime` service.
[Application](../reference/application.md), [Services](../reference/services.md)
and [Hosting](../reference/hosting.md) describe the ownership boundaries.

## Authentication and request validation

| Response | Meaning to investigate |
| --- | --- |
| 401 | Credential absent/invalid, wrong bearer versus browser credential, or wrong application's data root |
| 403 | Policy refused authorization; also check the starter's exact Host/Origin requirements |
| WebSocket 1008 before accept | Handshake/authentication/authorization rejected; browser sockets cannot set arbitrary bearer headers |
| 400 / 415 / 422 | Malformed JSON, unsupported body type, or request schema/model validation; inspect the safe error response |
| 500 / 502 | Declared response contract, upstream or unexpected server failure; correlate error id with logs, do not expose secrets |
| Preview can read but not send/save/start voice | Intended read-only privilege; use the principal client for writes, not broader preview permissions |

Applications supply the policy; Framework is not a user database. Opening a
listener or trusting a private network does not create client authentication.
Do not solve a credential/origin failure by exposing a wildcard listener,
putting long-lived credentials in a URL or disabling the security policy.
See [Security](../reference/security.md).

## Conversation, tools and model failures

- **UI works, model login fails:** authentication is lazy. Use the application's
  explicit SDK login flow from the intended environment, then retry deliberately.
  Never paste credentials into configuration. Account/model availability is an
  external prerequisite; changing UI code cannot repair it.
- **Tool missing:** distinguish installation, enabled binding, registry/schema
  and model-side selection. A declared service is not automatically a worker tool.
  Verify the namespace/function name, reserved feedback and argument schema in
  [Tools](../reference/tools.md). Do not assume a model will choose a tool because
  an offline test called it successfully.
- **A call returned only status/message IDs:** this is the acknowledgement.
  Read the corresponding canonical output/artifact items; do not treat it as
  the business result. See the tool result contract.
- **Display duplicated or missing after reconnect:** reconcile against archive
  pages/canonical item IDs and fresh socket state, not transient deltas. Canonical
  history, display projection and current model context are different views.
- **Provider/context disappeared:** live providers are sampled, ephemeral context,
  not saved messages. Persist conclusions explicitly where needed; compaction
  does not snapshot browser/device state.
- **Restart left a partial call/task:** inspect canonical recovery and the durable
  queue/journal before retrying effects. Reconnection and deduplication do not
  make arbitrary external tool effects exactly-once.

See [Conversation](../reference/conversation.md), [Sessions](../reference/sessions.md)
and [Context](../reference/context.md) for the precise recovery/projection rules.

## Media, devices and scheduling

An audio/image file, artifact or accepted delivery request does not establish that
a person saw/heard it. Browser permissions and remote capability leases can fail
independently of backend readiness. Separate generated, routed, started,
completed and acknowledged states. Realtime captions/deltas are temporary until
canonical transcript commit; voice session acceptance is not microphone connection.
Use [Realtime](../reference/realtime.md) and [Client integration](client-integration.md).

For schedules, check offset datetime/IANA timezone, current task revision and
occurrence, then receipts and runtime health. A delivered reminder is not task
completion. A native client being armed is not fired, and firing is not human
receipt; service-only scheduling does not supply your app's pairing/ACK routes.
Never escalate message → alarm or contact someone merely because a response
is late. See [Work and scheduling](../reference/work-and-scheduling.md).

## Source changed, application unchanged

Check the installed import location and active process/image/release. Source
edits, UI builds, backend proof, backend selection and activation are different
operations. A published UI takes precedence over a source bundle; an unpublished
preview survives restart but is not active. Immutable `/workspaces` edits do not
replace site-packages. Use the documented supervisor/release lifecycle, not a
blind process kill. Image rollback does not undo production data changes.

For Docker recovery, inspect `Deployment.status()` **and** live health. A selected
candidate is not `current`; a recorded `current` need not still be alive. A failed
retest removes proof/selection; launch also checks matching application options.
Unexpected exits require investigation and explicit recovery, not an infinite
restart loop. Keep prior images and backups available.
See [Deployment](../deployment.md) and [Surfaces](../reference/surfaces.md).
