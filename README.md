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
newer is required.

To install the published package in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install b4pt0r-the-framework
```

The Python wheel includes the framework and the editable starter template, but
not a prebuilt browser UI or Playwright's Chromium binary. Those are prepared
separately when you run the starter.

## What an application is made of

| Part | What it does | What your application decides |
| --- | --- | --- |
| `AgentApplication` | Validates and assembles one application | Name, version, plugins, server services, security and client surfaces |
| `AgentSpec` | Describes an agent and its session policy | Instructions, model configuration, tools and resources |
| `Plugin` | Owns one modular feature, including its agent binding, server runtime and dependencies | Which capabilities exist and when they are exposed |
| `Extension` / `@endpoint` | Describes a server component and its validated API routes inside a plugin | Service lifecycle, route schemas and authorization |
| `ClientSurface` | Builds, previews, publishes and rolls back an editable web UI | Source, build command, routes and release policy |

There is exactly **one primary, durable conversation**. Private specialist
agents can perform bounded background work, but their sessions are not new
user-facing conversations and they cannot edit the primary history directly.
The server supervises workers and owns authentication and client transport;
workers own inference and their own sessions; a browser client owns its local
interface and device/media execution. This separation lets a client reconnect
or refresh while the main conversation remains intact.

To implement agent-side tools and hooks, subclass `AgentPlugin` from
`the_framework.agent` and pass that class as `Plugin(agent=...)`. A plugin may
have agent-side behavior, server-side behavior, or both. Its
plugin's runtime state and its binding to an agent are separate: turning off its
tools for the agent need not remove routes that a settings screen still uses.
Specialists, durable task delivery and versioned public capabilities are
available when a simple plugin is not enough. More detail is in the
[framework guide](https://github.com/B4PT0R/the_framework/blob/main/docs/framework.md).

## Try a working application

Install [`uv`](https://docs.astral.sh/uv/) and Node.js/npm, then from this
checkout run:

```sh
uv sync
npm --prefix starter/ui ci
npm --prefix starter/ui run build
uv run playwright install chromium
uv run python -m starter
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

Both directories must be empty or absent, distinct and non-overlapping. The
command copies the starter source and records the private data location; it
does not install npm dependencies, build the UI or launch the app. From the new
code directory, run `npm --prefix starter/ui ci`,
`npm --prefix starter/ui run build`, then `python -m starter` in an environment
with `b4pt0r-the-framework` installed. Change `starter/application.py` and
`starter/instructions.md` first; `starter/ui/` contains the editable interface.
Keep the data directory private: it holds conversation history, configuration,
uploads, memory and browser profiles.

## Build a small application yourself

Save the following as `example.py`. It is a complete HTTP application; the
repository also includes an [expanded version](https://github.com/B4PT0R/the_framework/blob/main/examples/minimal_agent_app.py)
with `modict` request/response models and a health endpoint.

```python
from the_framework import AgentApplication, AgentSpec, Extension, Plugin, SessionPolicy, endpoint
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
application = AgentApplication(
    name="Counter Agent",
    version="0.1",
    primary_agent=AgentSpec(
        name="assistant",
        description="The application's primary agent",
        session=SessionPolicy.durable(),
    ),
    security=BearerSecurity(),
    plugins=(Plugin(name="counter", runtime=Extension(
        name="counter_runtime", service=counter,
        endpoints=(counter.increment,),
    )),),
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
OpenAPI schema. `Extension` manages dependencies and service startup/shutdown;
`AgentApplication.compile()` checks the full graph before runtime startup.

For the next step, read [composition and runtime](https://github.com/B4PT0R/the_framework/blob/main/docs/framework.md) for
plugins, private specialists, persistent resources, security, voice and client
surfaces. The [starter source](https://github.com/B4PT0R/the_framework/tree/main/starter) shows these features in one application
without requiring you to adopt its UI design.

## Develop and package

```sh
uv sync --extra dev
uv run pytest -q tests/application tests/agentic
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
