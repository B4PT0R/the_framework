# Your first application

[Documentation](../README.md) / Build / First application

Create a local desktop agent application with a Python backend and a React
interface in Chromium. By the end, you will have exchanged a message, changed
the agent’s instructions and reopened the same conversation.

The starter supplies the working application. You choose its editable code
location and a separate location for private data.

## 1. Check the prerequisites

Use Python 3.12 or newer, `uv`, and Node.js/npm. Playwright Chromium also requires
system browser libraries. Install missing OS dependencies deliberately with the
permissions appropriate to your system; bootstrap does not install them.

You will choose two **empty or absent, distinct and non-overlapping** directories:

- a code directory for source you can edit;
- a private data directory for conversation, settings, plugin stores and files.

Keep private data out of source control. The starter’s command tools execute
with your account’s permissions; review its installed features and instructions
before giving it work.

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
The next step connects a model account so the agent can reply. The starter
listens on loopback and installs browser credentials privately; keep that port
local.

## 3. Make the first reply possible

The UI and history can start before a model account is available. Inference uses
`codex-backend-sdk`; it needs usable credentials and account/model capabilities.
To explicitly authenticate, run this in the generated app’s environment:

```sh
cd /path/to/my-agent-code
uv run python -c 'from codex_backend_sdk import OpenAI; OpenAI().authenticate()'
```

Then send a short message and check that a reply arrives. If necessary, select
an available model in Settings. This establishes text chat; optional features
such as voice have their own setup. Keep credentials in the backend’s account
store. See [Backend login](../../starter/README.md#backend-login) for the
starter’s account behavior.

## 4. Find the source you will change

In the generated project:

| File | Your first reason to open it |
| --- | --- |
| `starter/application.py` | Choose agent recipes, model defaults and installed plugins |
| `starter/instructions.md` | Change identity and behavior; the supplied personality is editable |
| `starter/memory.md` | Customize the private memory curator |
| `starter/ui/src/` | Change the interface |
| `starter/server.py` | See the single build call and explicit storage/security inputs |
| `starter/security.py` | Understand the starter’s loopback access policy before changing transport |

These are the same sources an agent with authorized development tools can
work on. Start with a manual change to see how source, activation and saved
conversation fit together.

## 5. Change the instructions and resume

Add a short preference to `starter/instructions.md`, such as “Use concise replies
and include file paths when discussing code.” Stop the local application and
relaunch it with the same code and data locations:

```sh
the-framework run --code-dir /path/to/my-agent-code
```

The starter reads the instructions when constructing the agent. Your previous
exchange should still appear. Ask a new question to see the updated instructions
in use. Changes to application/server source likewise take effect when the
application is reconstructed.

Saved session settings take precedence over recipe defaults. To change the model
of an existing conversation, use Settings. To add executable behavior, continue
with [Your first plugin](first-plugin.md).

## 6. Repair setup when needed

`run` reuses the configured data directory and successful setup stages, checking
Python dependencies and whether Chromium can launch. Keep that data directory
to retain the conversation. Save any local UI drafts before closing the window.

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

For versioned backend images and supervised activation, continue later with
[Deployment and recovery](../deployment.md).
