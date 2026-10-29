# Your first application

[Documentation](../README.md) / Build / First application

**Goal:** create an independent, editable local application, launch its interface
and know where to make your first change. You do not need to understand server
composition or deployment first.

## 1. Check the prerequisites

Use Python 3.12 or newer, `uv`, and Node.js/npm. Playwright Chromium also requires
system browser libraries. Install missing OS dependencies deliberately with the
permissions appropriate to your system; bootstrap does not install them.

You will choose two **empty or absent, distinct and non-overlapping** directories:

- a code directory for source you can edit;
- a private data directory for conversation, settings, plugin stores and files.

Keep private data out of source control. Tools in this application can use real
account privileges; this setup is not a sandbox.

## 2. Install and bootstrap

In a Python environment of your choice (for example a virtual environment):

```sh
python -m pip install b4pt0r-the-framework
the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data --run
```

Replace the example paths. The command copies the starter, creates its project
manifest and `.venv`, prepares Chromium and npm dependencies, builds the UI, then
launches it. Its code remains yours to edit. The installed Framework version is
pinned; an editable Framework checkout is linked explicitly instead.

**Expected result:** a local Chromium window with conversation and settings.
Preparation and opening the UI do not prove that inference credentials work.
The starter listens on loopback and installs browser credentials privately.
Do not make its port publicly accessible.

## 3. Make the first reply possible

The UI and history can start before a model account is available. Inference uses
`codex-backend-sdk`; it needs usable credentials and account/model capabilities.
To explicitly authenticate, run this in the generated app’s environment:

```sh
cd /path/to/my-agent-code
uv run python -c 'from codex_backend_sdk import OpenAI; OpenAI().authenticate()'
```

Then send a short message. A successful reply verifies text inference for that
account and model—not voice, embeddings or every optional service. If necessary,
select an available model in Settings. Never paste credentials into application
configuration. See [Backend login](../../starter/README.md#backend-login) for the
starter’s account behavior.

## 4. Find the application, not just its configuration

In the generated project:

| File | Your first reason to open it |
| --- | --- |
| `starter/application.py` | Choose agent recipes, model defaults and installed plugins |
| `starter/instructions.md` | Change identity and behavior; the supplied personality is editable |
| `starter/memory.md` | Customize the private memory curator |
| `starter/ui/src/` | Change the interface |
| `starter/server.py` | See the single build call and explicit storage/security inputs |
| `starter/security.py` | Understand the starter’s loopback access policy before changing transport |

Start with a small instruction change. Application/server source changes require
reconstruction of the running application; do not expect an imported declaration
to hot-reload. Saved session settings take precedence over declaration defaults,
so edit an existing conversation’s model in Settings rather than assuming a new
source default overrides it. For adding code behavior, continue with
[Your first plugin](first-plugin.md).

## 5. Stop, relaunch and recover setup

Stop the local application deliberately when finished, then launch the same
project with:

```sh
the-framework run --code-dir /path/to/my-agent-code
```

It reuses the configured data directory and successful setup stages, checking
Python dependencies and whether Chromium can launch. Existing retained history
should appear again. This is session continuity; it does not restore unsaved UI
drafts or undo external tool effects.

If preparation failed, correct the reported prerequisite and resume without
recreating or overwriting your source:

```sh
the-framework prepare --code-dir /path/to/my-agent-code
```

Use `bootstrap --no-prepare` for source only. `run` also accepts `--headless`,
`--no-browser` and `--port`; `--no-browser` is not a remote hosting mode.
For checkout-based setup instead of a generated app, see
[Development](development.md#run-the-starter-from-source).

## Where to go next

- [First plugin](first-plugin.md) to add one tool.
- [Starter manual](../../starter/README.md) for attachments, voice, settings,
  private data, UI releases and verification commands.
- [Architecture](../explanation/architecture.md) to understand assembly.
- [Reference](../reference/README.md) only when you need a specific contract.

Immutable backend deployment is optional and separate. You do not need Docker
to follow this guide.
