# Local Agent starter

A small, editable desktop application built with `the_framework`: one persistent
conversation, a Python worker, an authenticated loopback server and a Chromium
window. This is application source to make your own, not a mandatory framework UI.

## Create your own copy

From an environment with `b4pt0r-the-framework` installed, run `the-framework bootstrap`
(or `python -m the_framework bootstrap`). Enter an empty directory for editable
application code and a different empty directory for private application data.
The CLI copies this complete starter, records the data location in
`starter/instance.json`, and never overwrites an existing nonempty directory.
You can also pass `--code-dir` and `--data-dir` non-interactively. Then, from the
new code directory:

```sh
npm --prefix starter/ui ci
npm --prefix starter/ui run build
python -m playwright install chromium
python -m starter
```

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

General plugins include Bash, registry, web search, memory, chat, artifacts, scheduler,
system, browser and realtime declarations. The `chat` plugin owns the prompt
and attachment HTTP routes; it has no agent-side binding, so settings show its
server status without an agent toggle. **Loaded** describes local runtime state,
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
| `server.py` | One `build_conversation` call with local security and storage inputs |
| `security.py` | Exact host/origin checks and private local authentication |
| `desktop.py` | Parent-owned browser/socket and supervised server replacement |
| `ui/src/main.jsx` | Chat, composer and connection lifecycle |
| `ui/src/settings.jsx` | Model, plugin activation and editable configuration |
| `ui/src/session.js` | Display projection of canonical items and streaming events |

Import a complete framework plugin and add it to `ApplicationSpec.plugins`:
`SCHEDULER`, `SYSTEM` and `REALTIME` own their agent bindings, server services,
routes and lifecycle. Their server implementations are deferred so the worker
can compile the same application declaration without constructing server services.
`server.py` supplies the data directory, plugin options and restart callback to
`build_conversation`; the framework constructs the worker and canonical transport.
The memory declaration shows an explicit specialization of the complete `MEMORY`
plugin with application-owned curator instructions. The optional shared `CHAT` plugin owns the prompt and attachment routes.

Custom plugins should expose their complete `Plugin` declaration from their
package; keep agent and server implementations inside it. Backend contributions
use `ServerExtension` and `@endpoint`. Keep product policy in application
instructions and plugins; the canonical worker remains the sole history writer.

The system/browser plugins expose managed UI build, preview, publish and rollback.
Edit `ui/`, build a candidate, inspect its authenticated preview in the separate
browser context, then publish. Publication refreshes the UI; the conversation
remains on the server. The supervised restart action replaces the server/worker
while retaining the browser and listening address. These facilities are a
development workflow, not a sandbox: the agent's Bash capability executes with
the user's OS permissions.

## Verification and boundaries

```sh
npm --prefix starter/ui test
npm --prefix starter/ui run build
.venv/bin/pytest -q tests/application/test_starter.py \
  tests/application/test_starter_security.py tests/application/test_starter_desktop.py
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run pytest -q \
  tests/application/test_starter_browser_recovery.py
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
`STARTER_LIVE_TESTS=1 .venv/bin/pytest -q tests/application/test_starter_live.py`.
It uses temporary data and checks an innocuous Bash `printf` plus a separate
memory request, curator completion, embeddings and retrieval after reconstruction.
Both tests are skipped by default.
For the separate audio test, build the UI and run
`STARTER_LIVE_TESTS=1 .venv/bin/pytest -q tests/application/test_starter_voice_live.py`
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
