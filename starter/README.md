# Local Agent starter

A small, editable desktop application built with `the_framework`: one persistent
conversation, a Python worker, an authenticated loopback server and a Chromium
window. This is application source to make your own, not a mandatory framework UI.

## Create your own copy

From an environment with `b4pt0r-the-framework` installed, plus `uv` and
Node.js/npm:

```sh
the-framework bootstrap --code-dir /path/to/app --data-dir /path/to/private-data --run
```

Or omit the directory flags to enter them interactively. Both directories must
be empty/absent and separate. The CLI copies the starter, writes a project
manifest, creates the app's `.venv`, prepares Chromium, installs npm dependencies,
builds the interface and starts it. Source stays editable and a nonempty project
is never overwritten. Omit `--run` to prepare without launching.

To launch again, or resume setup after correcting a missing dependency:

```sh
the-framework run --code-dir /path/to/app
the-framework prepare --code-dir /path/to/app
```

`run` checks that Python dependencies are synchronized and Chromium can actually
launch, installing only missing or outdated resources. It reuses successful npm
stages and rebuilds edited UI sources. Explicit `prepare` refreshes Python and
Chromium even when those checks pass, so it also serves as a repair command.
It also accepts `--headless`, `--no-browser` and `--port`. Inside the generated
project you can use `uv run the-framework run`. `bootstrap --no-prepare` copies
source without installing dependencies. System browser libraries and account
sign-in remain explicit prerequisites; no inference is performed by preparation.

The launcher's `--data-dir` option can override the generated data location.
Keep `starter/instance.json` private if the path itself is sensitive. The UI
contains its own small WebRTC/caption helpers; its npm dependencies do not
include The Harness product packages.

## Run from this repository

Requires Python 3.12+, Node/npm and the system libraries required by Playwright
Chromium. The starter is bundled as a bootstrap template in this framework's
wheel. Install `b4pt0r-the-framework` from PyPI or use `uv sync` from this
checkout before running these commands.

```sh
python3 -m venv .venv
uv sync --extra dev
npm --prefix starter/ui ci
npm --prefix starter/ui run build
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run python -m starter
```

On Linux, Playwright's `install --with-deps chromium` can also install required
system libraries (requires appropriate administrative permission).

Use `--data-dir /path/to/my-agent` for another application's data, `--port 48890`
for a fixed loopback port, or `--headless` for automated browser checks.
`--no-browser` launches only the supervised server and private browser RPC; it
does **not** make the HTTP API publicly accessible. Credentials are installed by
the launcher as HttpOnly cookies, never passed in URLs or browser storage.

The default data directory is `~/.local/share/local-agent`. It contains the
canonical session, configuration, plugin state, uploaded files, memory and browser
profiles. Keep it private. Another data directory creates another independent
application with separate state.

### Backend login

The window, history and settings can start without backend credentials. Inference
authenticates lazily and reports a missing login without opening an OAuth browser
from the worker. To explicitly sign in using the installed backend SDK, run:

```sh
.venv/bin/python -c 'from codex_backend_sdk import OpenAI; OpenAI().authenticate()'
```

Then retry the message; a failed authentication is not cached. The SDK reuses its
stored Codex credentials when available. Credentials are not application settings
and must never be pasted into the configuration JSON. Account/model availability
and changes to the backend remain external prerequisites.

## Everyday use

- Send text, follow streamed responses and interrupt with **Stop**.
- **Earlier messages** retrieves older retained conversation turns.
- **Attach** stages files; remove them with × before sending. Files are copied
  before the turn enters the worker queue. A message is optional. Supported
  images are validated and sent as vision inputs; other files are referenced by
  their local path for tools to read. Each upload is limited to 32 files / 64 MiB.
- **Settings** selects the model and enables plugin contributions. **Advanced
  configuration** edits the same configuration draft, including agent-plugin settings.
  Save applies and persists it. Model availability depends on the backend account.

General plugins include Bash, registry, web search, memory, artifacts, scheduler,
system, browser and realtime declarations. The host owns prompt and attachment
HTTP routes through `build()`; chat is not a plugin or a settings
toggle. **Loaded** describes plugin runtime state,
not confirmation of external-service availability. Inference, hosted search and
memory embeddings require a usable backend account and supported capabilities.
The starter defaults to `gpt-5.6-luna`; saved model settings take precedence.
The application can start and display settings/history without a successful
inference request; request failures are reported in the chat.

**Voice** connects the browser microphone through the starter's WebRTC transport and
the core realtime controller. **Cancel voice / End voice** releases the peer and
server session. Typed text joins the live conversation; stop voice before sending
attachments. Partial transcripts appear as temporary **Live** captions, replaced
by canonical messages when complete. A real backend exchange has been verified
in Chromium with a simulated microphone; physical microphone quality and OS
permission behavior still depend on the user's environment.
Remove the `realtime` plugin declaration in `application.py` for a text-only
application; the starter's chat routes and composer continue working and the
Voice control disappears when that runtime is absent.

## Make it your application

| File | Responsibility |
| --- | --- |
| `application.py` | Agent identity, plugin composition, persistence and UI surface |
| `instructions.md` | Main agent's editable instructions |
| `memory.md` | Private memory curator's instructions |
| `server.py` | One `build` call with local security and storage inputs |
| `security.py` | Exact host/origin checks and private local authentication |
| `desktop.py` | Parent-owned browser/socket and supervised server replacement |
| `ui/src/main.jsx` | Chat, composer and connection lifecycle |
| `ui/src/settings.jsx` | Model, plugin activation and editable configuration |
| `ui/src/session.js` | Display projection of canonical items and streaming events |

`application` is the global owner of this server. `build()` returns its ASGI
adapter; the lifespan starts/stops the services and reserves `Application.current()`.
The object stays inert when imported by a worker. Additional sessions share that
application and keep their own `SessionRuntime`; they do not construct more apps.

Import a complete framework plugin and add it to `Application.plugins`:
`SCHEDULER`, `SYSTEM` and `REALTIME` own their agent bindings, server services,
routes and lifecycle. Their server implementations are deferred so the worker
can compile the same application declaration without constructing server services.
`server.py` supplies the data directory, plugin options and restart callback to
`build`; the framework constructs the worker, text/file ingress and canonical transport.
The memory declaration shows an explicit specialization of the complete `MEMORY`
plugin with application-owned curator instructions. Text/file ingress is always
available, with optional realtime coordination through the shared voice lock.
Plugin bindings change agent exposure while leaving installed services available.
Changing one during voice closes the old call and reconnects its owning client
from the updated conversation snapshot. Manual stop takes precedence; disabling
the voice binding ends the call without reopening it. Installing/removing a plugin
is a separate startup-composition change, expected to be infrequent.


Custom plugins should expose their complete `Plugin` declaration from their
package; keep agent and server implementations inside it. Backend contributions
use `ServerExtension` and `@endpoint`. Keep product policy in application
instructions and plugins; the canonical worker remains the sole history writer.

The system/browser plugins expose managed UI build, preview, publish and rollback.
These are agent tools; the base settings UI does not expose deployment controls.
Edit `ui/`, build a candidate, inspect its authenticated preview in the separate
browser context with `system.preview_surface(name="main")`, then publish.
The tool handles private authentication through a one-use link. Only one principal
desktop may connect; previews use separate read-only event connections and cannot
claim its application bridge. Preview drafts and local settings navigation are
testable, while prompts, voice and persistent writes are blocked. Named mobile
or Quest surfaces use the same tool without disturbing the principal desktop. Publication refreshes the UI; the conversation
remains on the server. The supervised restart action replaces the server/worker
while retaining the browser and listening address. These facilities are a
development workflow, not a sandbox: the agent's Bash capability executes with
the user's OS permissions.

The desktop launcher owns the listener and browser RPC for its whole lifetime;
each replaceable HTTP child uses the Framework's `LocalServer`. Readiness checks
require a healthy worker and a descriptor belonging to the new child. Browser
cookies use a persistent private key; control tokens rotate with the server.
Stopping the server child leaves the page intact; stopping the desktop launcher
closes its child, RPC connections and Chromium contexts.

Each surface adopts its declared compiled artifact on first startup. Published
releases take precedence over source bundles, and unpublished previews survive
server restarts. A missing initial bundle leaves the server available for repair.

## Verification and boundaries

```sh
npm --prefix starter/ui test
npm --prefix starter/ui run build
.venv/bin/pytest -q tests/general/test_starter.py \
  tests/general/test_starter_security.py tests/general/test_starter_desktop.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run pytest -q \
  tests/general/test_starter_browser_recovery.py
```

Run these commands from the repository root. The local browser directory is
Git-ignored and must match this environment's Playwright version; reinstall after
updating Playwright. Keeping it separate avoids inheriting another application's
incompatible `PLAYWRIGHT_BROWSERS_PATH`. Use the same explicit path when running
the full Python suite with browser coverage enabled.

The last test uses isolated data and an actual Chromium/worker/server. It checks
reconnection, retained history, attachments selection, settings persistence and
the edit → preview → publish → rollback workflow. It makes no model calls and
does not establish voice, model or embedding service availability. End-to-end
text inference, a Bash tool roundtrip, and memory curation/indexing/retrieval have
been checked with isolated real workers and backend calls. The opt-in voice test
checks WebRTC connection, received audio, live captions, persisted transcription
and cleanup with Chromium's fake microphone (never the user's microphone).

To explicitly spend backend quota on a short tool/inference smoke test, run
`STARTER_LIVE_TESTS=1 .venv/bin/pytest -q tests/general/test_starter_live.py`.
It uses temporary data and checks an innocuous Bash `printf` plus a separate
memory request, curator completion, embeddings and retrieval after reconstruction.
Both tests are skipped by default.
For the separate audio test, build the UI and run
`STARTER_LIVE_TESTS=1 .venv/bin/pytest -q tests/general/test_starter_voice_live.py`
with Playwright Chromium installed.

This starter is local-only for now. It does not ship remote pairing, a mobile
shell or a public deployment configuration. Never expose its port publicly as a
substitute for an authenticated remote-client implementation.

### Presenting artifacts

The bundled `ARTIFACTS` plugin can present images and downloads while preserving
published local files. Extensions may return `Artifact(kind=..., payload=...)`
directly; tools/commands persist it and providers display it only during the
current turn. `Image` remains vision-only. Add a React renderer through `App`'s
`artifactRenderers` prop for a custom widget kind; unknown kinds fall back to JSON.
See [the artifact contract](../docs/framework.md#conversation-artifacts).

All uploaded and published client files share `data_root/bucket/`, including images,
video and other media. `ARTIFACTS` serves this tree through authenticated
`/api/v1/bucket/...` URLs. Application-private state stays outside the bucket.

The starter presents one default conversation. The server can additionally host
independent sessions via `/api/v1/sessions`; each owns its worker, history, settings
and bindings. See the Framework's independent application sessions documentation
for scoped HTTP and WebSocket endpoints. Static defaults are declared in
`AgentSpec.configuration`; a resource factory is only needed for dynamic,
process-local inputs. The canonical session persists changes without separate
configuration/state files.

The host serves chat history using the built-in `display` projection: user and
assistant messages plus artifacts. Vision-only images remain internal. No Worker
import or projection registration is needed. Custom views remain available through
`AgentSpec.projections` and `Application.build(session_projection=...)`.

The starter deliberately leaves session reset unavailable. To enable it, provide a
custom `SessionRuntime` through `session_factory` and override its async
`reset_session()` method to select the transaction and return the reset result.
There is no separate reset callback to wire into the server.

The chat and voice clients consume the Framework's public events, not Responses
or Live wire signals. Streaming text is provisional; committed session items
supply replay. Voice captions receive cumulative `voice.transcript.updated` text
and clear on `voice.turn.completed`. Plugin-specific events belong in the plugin.

### Desktop interface convention

The application does not need a `ClientSurface` declaration for the usual case:
`Application.build("starter.application:application", ...)` locates `starter/ui/`,
builds with `npm run build` into `dist` and serves the main desktop surface at `/ui`.
The UI stays editable application source. Use `surfaces=()` for an explicitly
headless application, or declare custom `ClientSurface` values for other source
paths, builds or mobile/Quest interfaces. Only nonstandard settings need repeating.

### Adding a server feature

Declare a `Plugin` with a `ServerExtension(service_factory=YourService)`.
The constructor can request `context` for paths/options and named services such as
`runtime`; the framework orders their construction and startup. Decorated HTTP
methods and `websocket_declarations()` are discovered on the constructed service.
Use a `module:factory` reference to keep server implementation imports out of the
agent worker. The starter uses the bundled plugins' storage defaults; only pass
`BuildContext(plugins={...})` when overriding them. See the Framework reference for
advanced inter-plugin contributions and session/application scopes.

The worker also owns context preparation and compaction: applications use
`agent.compact()` or the existing worker command, without constructing a compaction
controller. The same canonical session continues to supply display history after
compaction; ephemeral provider output belongs only to generation, not the archive.

Private specialists are declared as AgentSpec values or `@agent_trigger` bindings
on their plugin. The worker resolves their configured catalogue after extensions
load; application code does not assemble worker profiles. Submit work with
`agents.submit(...)` (a request ID, not a blocking result). Saved session settings
remain authoritative over declaration, resource and resolved launch defaults.
