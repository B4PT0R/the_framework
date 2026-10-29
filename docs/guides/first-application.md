# Run and change the starter

This path creates an editable Python/React desktop application. It does not
require writing a server. You will separate source from private data, obtain a
model reply, change an instruction and resume the existing conversation.

## Prerequisites

- Python 3.12 or newer and `uv` on `PATH`;
- Node.js and npm on `PATH`;
- OS libraries required to launch Playwright Chromium, and a graphical session
  for the visible window;
- network access to install Python/npm packages and Chromium, then usable backend
  credentials and model access for inference.

Bootstrap installs the browser binary, **not** its OS dependencies. Install missing
system libraries deliberately. The launcher and generated environment use
Unix-style executable paths; do not infer validated Windows support from Python
package metadata.

In this suite's development workspace, use the existing shared environment and
[checkout workflow](development.md) instead of creating nested environments.
The commands below are for a separate consumer application.

## 1. Install and copy

Choose **absent or empty, distinct, non-overlapping** absolute directories:

```sh
python -m pip install b4pt0r-the-framework
the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data --run
```

What this does:

1. Copies the packaged starter to `my-agent-code/starter/`.
2. Writes a project manifest pinned to the installed Framework version. If the
   CLI itself comes from an editable Framework checkout, it writes an explicit
   editable source link instead.
3. Creates the private data directory with mode `0700` and writes its location to
   `starter/instance.json` (gitignored).
4. Uses `uv` to prepare `my-agent-code/.venv`; installs Playwright Chromium when
   missing or unable to launch; runs npm install/build stages as needed.
5. Starts the local desktop application. Port `0` selects a free port.

The expected result is a Chromium window containing history and settings. Opening
a window is not evidence that inference works. The copied app contains powerful
shell/browser features; review `starter/application.py` and its instructions
before asking it to modify files. Commands use the configured account's real
permissions, not a sandbox.

Keep the data directory private and outside Git. The code can be versioned;
`.venv`, `.framework`, `starter/instance.json` and generated UI files are ignored.

## 2. Authenticate and get a reply

Inference uses `codex-backend-sdk`. The default Framework client authenticates
noninteractively; it does not initiate an interactive login from a turn. To log
in explicitly using the generated application's environment:

```sh
cd /path/to/my-agent-code
uv run python -c 'from codex_backend_sdk import OpenAI; OpenAI().authenticate()'
```

Follow the SDK's sign-in flow. Do not paste credentials into source, a prompt or
an issue. Select a model supported by your backend/account in Settings and send
`Reply with one sentence.` Check that an assistant message is committed, not
merely that a turn spinner appears. Defaults in source are not an availability
promise; see [Backend login](../../starter/README.md#backend-login).

Voice and other integrations have separate prerequisites; text working does not
validate them. [Agents](../reference/agents.md) provides an offline execution
example if you want to inspect library behavior before configuring an account.

## 3. Make one change and reopen the conversation

Find these copied application files:

| Path | Ownership |
| --- | --- |
| `starter/application.py` | Agent recipe, model defaults, installed plugins |
| `starter/instructions.md` | Primary agent instructions |
| `starter/memory.md` | Private memory curator instructions |
| `starter/server.py` | `Application.build`, explicit storage/security inputs |
| `starter/security.py` | Local authentication and origin policy |
| `starter/ui/src/` | Editable React interface |

Append `When discussing source code, include file paths.` to
`starter/instructions.md`. Stop this development application normally, then run:

```sh
the-framework run --code-dir /path/to/my-agent-code
```

The same `starter/instance.json` selects the same private data. The worker
reconstructs its recipe and reads the updated instruction file. Confirm the old
exchange is present, then ask a source question to exercise the instruction.
Do not delete data or generate a second application merely to reload code.

Saved session configuration overrides recipe defaults: editing the default model
in `application.py` does **not** replace the model already saved for an existing
conversation. Use Settings for that change. Python/server declarations require
reconstruction; this source launcher is not a general hot-reload system.
Managed UI publication and supervised immutable backend replacement are separate
features described in [Surfaces](../reference/surfaces.md) and
[Deployment](../deployment.md).

## Setup and launch commands

| Command / option | Exact purpose |
| --- | --- |
| `bootstrap --no-prepare` | Copy source and create data/manifest only; skip `uv`, npm and browser preparation. Mutually exclusive with `--run`. |
| `prepare --code-dir PATH` | Resume setup without overwriting source/data; explicitly refresh Python and Chromium checks, then reuse matching npm checkpoints. |
| `run --code-dir PATH` | Check the actual Python environment and headless browser launch, reuse successful npm stages and launch. Defaults to current directory when omitted. |
| `run --port N` | Choose a local port; default `0` chooses a free one. |
| `run --headless` | Launch browser without a visible window. |
| `run --no-browser` | Do not launch a browser; does not enable remote hosting or weaken access policy. |

Preparation stores npm stage fingerprints in `.framework/prepare.json`.
A failed subprocess leaves source and private data intact and prints the failing
command. Fix that prerequisite, then run `prepare` again. A stale marker is not
accepted as proof that Python dependencies or Chromium are usable.

## Diagnose the first failure

| Symptom | Check |
| --- | --- |
| `Missing prerequisites` | `uv`, `node`, `npm` must all resolve in the launcher's environment. |
| Chromium installed but launch fails | OS libraries and graphical/session permissions; the preparation check launches headless Chromium. |
| No model reply | Explicit login in the **generated app environment**, model/account access and the reported backend error. |
| Changed default has no effect | Existing saved settings take precedence. Change Settings or inspect the relevant extension's restart requirement. |
| History appears empty | Verify the selected data directory; do not overwrite or reset the original while investigating. |
| Routes inaccessible from another machine | Expected with loopback policy. Design and configure authenticated remote access; do not simply expose the port. |

Continue with the [Notebook tutorial](first-plugin.md) to build a feature spanning
storage, HTTP, worker tools and sampled context. The [starter manual](../../starter/README.md)
covers normal operation; [Security](../reference/security.md) covers hosting.

Source: [`cli.py`](../../the_framework/cli.py),
[`starter/application.py`](../../starter/application.py),
[`starter/server.py`](../../starter/server.py).
