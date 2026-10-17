# The Framework

The Framework is a Python library for building applications around a persistent
AI agent. It supplies the parts that are easy to get wrong repeatedly: one
durable conversation, a supervised agent worker, plugin and specialist-agent
lifecycle, authenticated HTTP/WebSocket connections, and versioned web
interfaces. You supply the agent's identity, instructions, application
services, security policy and user experience.

You can use it for a local desktop companion, a private agent with tools and
memory, or another agent application whose browser and backend need to survive
worker restarts without losing the conversation. It is a library, not a hosted
agent service. The included [starter](https://github.com/B4PT0R/the_framework/blob/main/starter/README.md) is an editable
application that shows the pieces working together; you are free to build a
different frontend or none at all.

The PyPI distribution is named `b4pt0r-the-framework`. Python code imports
`the_framework`, and the template command is `the-framework`. Python 3.12 or
newer is required. The examples below describe the 0.2 composition API in this
checkout; until 0.2 is published, run them using the checkout's `uv sync`
instructions below. The published package may still have the older API.

To install the published package in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install b4pt0r-the-framework
```

The Python wheel includes the framework and editable starter template. The
bootstrap CLI prepares its Python environment, browser UI and Chromium runtime;
these generated dependencies are not bundled in the wheel.

## What an application is made of

| Part | What it does | What your application decides |
| --- | --- | --- |
| `ApplicationSpec` | Validates and assembles one application | Name, version, plugins, server services, security and client surfaces |
| `AgentSpec` | Describes an agent and its session policy | Instructions, model configuration, tools and resources |
| `AgentExtension` | Contributes to one agent | Tools, instructions, providers, hooks, configuration and state |
| `Plugin` | Owns one modular feature, including its agent binding, server runtime and dependencies | Which capabilities exist and when they are exposed |
| `ServerExtension` / `@endpoint` | Describes advanced server components and validated API routes inside a plugin | Service dependencies, route schemas and authorization |
| `ClientSurface` | Builds, previews, publishes and rolls back an editable web UI | Source, build command, routes and release policy |

Bundled plugins are imported as complete features:

```python
from the_framework.plugins.scheduler import SCHEDULER
from the_framework.plugins.system import SYSTEM

# In your ApplicationSpec:
# plugins=(SCHEDULER, SYSTEM)
```

Their declarations include server wiring and lifecycle; applications supply
storage inputs and restart policy rather than reconstruct those integrations.

There is exactly **one primary, durable conversation**. Private specialist
agents can perform bounded background work, but their sessions are not new
user-facing conversations and they cannot edit the primary history directly.
The server supervises workers and owns authentication and client transport;
workers own inference and their own sessions; a browser client owns its local
interface and device/media execution. This separation lets a client reconnect
or refresh while the main conversation remains intact.

To implement agent-side tools and hooks, subclass `AgentExtension` from
`the_framework.agent` and pass that class as `Plugin(agent_extension=...)`. A plugin may
have agent-side behavior, server-side behavior, or both. Every plugin declared
at startup keeps its server routes and services until shutdown. Its binding to
the agent can change live: turning off tools does not remove routes that a
settings screen or safety control still uses. To remove the runtime, remove
the plugin from the startup declaration and restart the server.
Specialists, durable task delivery and versioned public capabilities are
available when a simple plugin is not enough. More detail is in the
[framework guide](https://github.com/B4PT0R/the_framework/blob/main/docs/framework.md).

## Compose conversation features without rebuilding their transport

`build_conversation()` includes text submission, atomic file attachments and the
text/voice mode barrier as host services. No chat plugin is needed; `REALTIME`
optionally adds voice:

```python
from the_framework.plugins.memory import MEMORY
from the_framework.plugins.registry import REGISTRY
from the_framework.plugins.realtime import REALTIME

# In your ApplicationSpec:
# plugins=(MEMORY, REGISTRY, REALTIME)
# build_conversation assembles the supervised runtime from BuildContext(data_root=...).
```

When voice is installed, memory and registry contribute their own live context
producers. Voice owns delivery and cleanup, without knowing which application
or feature produced the context. Without voice, those contributions create no
live producers or background tasks. An application can replace its memory
curation or voice instructions through the plugins' agent extensions without
rewriting the transport. The starter uses these same public features.

The neutral The Harness application is the canonical, more complete consumer:
it combines these declarations with a persistent browser, desktop lifecycle,
application-specific instructions and a user interface. Its needs are evidence
for useful primitives, not a reason to add application policy to the library.

## Assemble the conversation

The editable starter's server uses one assembly operation:

```python
from the_framework import BuildContext

app = application.build_conversation(
    "my_app.application:application",
    BuildContext(data_root=data_dir),
    security=security,
    handshake=security.handshake,
)
```

It constructs the primary worker supervisor, runtime, authenticated HTTP API and
WebSocket transports, and derives storage paths from the data directory. Supply
your own access policy; the framework never installs an implicit allow-all policy.
Advanced applications can customize runtime construction, transport handshakes,
projections and extra server extensions through this same operation.
Plugin-specific construction options live in `BuildContext(plugins={...})`;
services are injected into factories and exposed separately in the read-only
`context.services` view after startup. Inputs stay fixed, and feature contributions
are sealed before services are constructed.

## Try a working application

Install [`uv`](https://docs.astral.sh/uv/) and Node.js/npm, then from this
checkout run:

```sh
uv sync
npm --prefix starter/ui ci
npm --prefix starter/ui run build
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run playwright install chromium
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers" uv run python -m starter
```

Playwright Chromium also needs its system libraries; on Linux,
`uv run playwright install --with-deps chromium` can install them if you have
the required system permissions. The starter opens a local Chromium window with
chat, settings, file attachments and an editable React interface. Its server
listens on loopback and authenticates the browser. You can inspect the UI and
settings without model credentials; generating replies, using hosted search or
embeddings, and voice features require a usable account/capabilities through
`codex-backend-sdk`. The [starter guide](https://github.com/B4PT0R/the_framework/blob/main/starter/README.md) covers sign-in,
data storage and feature-by-feature behavior.

To make an independent, editable copy, run `the-framework bootstrap` in the
environment where you installed the package. From this checkout, prefix it
with `uv run`:

```sh
uv run the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data
```

Both directories must be empty or absent, distinct and non-overlapping. Install
`uv` and Node.js/npm first; missing prerequisites are reported before creation.
The command copies editable source, creates a `pyproject.toml` and a private
`.venv`, installs Chromium and UI dependencies, then builds the interface.
It pins the installed framework version; from a development checkout it links
that checkout explicitly in the generated manifest.

Start it with:

```sh
the-framework run --code-dir /path/to/my-agent-code
```

Add `--run` to `bootstrap` to create, prepare and start in one command. Use
`bootstrap --no-prepare` for source only, or `the-framework prepare --code-dir ...`
to resume a failed preparation without overwriting the app. `run` prepares first,
reusing successful stages and rebuilding changed UI sources. It accepts `--port`,
`--headless` and `--no-browser`. Commands also work as `python -m the_framework`;
inside the generated project, `uv run the-framework run` uses its own environment.

Change `starter/application.py` and `starter/instructions.md` first;
`starter/ui/` contains the editable interface. Keep the data directory private:
it holds conversation history, configuration, the file bucket, memory and browser
profiles. Backend account sign-in and optional capabilities follow the starter
guide; preparation does not make an inference request or install system packages.

## Build a small application yourself

Save the following as `example.py`. It is a complete HTTP application; the
repository also includes an [expanded version](https://github.com/B4PT0R/the_framework/blob/main/examples/minimal_agent_app.py)
with `modict` request/response models and a health endpoint.

```python
from the_framework import ApplicationSpec, AgentSpec, Plugin, endpoint
from the_framework.server.api.endpoints import Principal


class BearerSecurity:
    async def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer example-secret":
            return None
        return Principal(id="example-client", scopes=frozenset({"counter:write"}))

    async def authorize(self, principal, requirement, request):
        return requirement is None or requirement.get("scope") in principal.scopes


class Counter:
    def __init__(self):
        self.value = 0
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    @endpoint(
        "post", "/api/v1/counter/increment",
        request={"type": "object", "properties": {"amount": {"type": "integer"}},
                 "required": ["amount"], "additionalProperties": False},
        response={"type": "object", "properties": {"value": {"type": "integer"}},
                  "required": ["value"]},
        authorization={"scope": "counter:write"},
    )
    def increment(self, amount: int):
        """Increment the application counter."""
        self.value += amount
        return {"value": self.value}


counter = Counter()
application = ApplicationSpec(
    name="Counter Agent",
    version="0.1",
    primary_agent=AgentSpec(
        name="assistant",
        description="The application's primary agent",
        session_mode="durable",
    ),
    security=BearerSecurity(),
    plugins=(Plugin(name="counter", server_extension=counter),),
)
app = application.build()
```

Start it with:

```sh
uv run uvicorn example:app --host 127.0.0.1 --port 8000
```

Then, in another terminal:

```sh
curl -sS -X POST http://127.0.0.1:8000/api/v1/counter/increment \
  -H 'Authorization: Bearer example-secret' \
  -H 'Content-Type: application/json' \
  -d '{"amount": 2}'
```

The result is `{"value":2}`. The literal bearer secret is **only** for this
loopback demonstration; use a real authentication policy for an application.
The primary agent declaration alone does not create a chat API or start
inference: the starter shows how to add its supervised worker and client
transport. `@endpoint` validates request and response data and contributes an
OpenAPI schema. Passing a service directly to `Plugin.server_extension` discovers its
decorated routes and manages its `start()`/`stop()` lifecycle. For a feature
with several server components or dependencies on other services, return
`ServerExtension` declarations instead; there, `endpoints="service"` explicitly
enables route discovery, and omitting it exposes no routes.
During build, the combined dependency graph and routes are validated before
services start.

For the next step, read [composition and runtime](https://github.com/B4PT0R/the_framework/blob/main/docs/framework.md) for
plugins, private specialists, persistent resources, security, voice and client
surfaces. The [starter source](https://github.com/B4PT0R/the_framework/tree/main/starter) shows these features in one application
without requiring you to adopt its UI design.

## Develop and package

```sh
uv sync --extra dev
uv run pytest -q tests/general tests/agentic
uv build
```

The build produces a wheel and source archive in `dist/`. The wheel contains
the typed `the_framework` package, runtime prompts and the editable starter
template; it does **not** bundle Node modules or a prebuilt browser UI. The
test suite includes a wheel-only import/bootstrap check outside the checkout.
For UI changes, also run `npm --prefix starter/ui test` and
`npm --prefix starter/ui run build`.

This repository is [MIT licensed](https://github.com/B4PT0R/the_framework/blob/main/LICENSE). See the [changelog](https://github.com/B4PT0R/the_framework/blob/main/CHANGELOG.md)
for version notes. Publishing to PyPI is a separate release action; building
locally does not publish anything.
