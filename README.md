# The Framework

The Framework is a Python library for applications built around a long-running
AI agent. It takes care of the conversation, tools, plugin lifecycle, worker
processes and authenticated server connections. Your application supplies the
agent's purpose, instructions, features and interface.

An application has **one main conversation** that survives a restart. Plugins
can add tools and services, or run specialist agents for private tasks; those
specialists do not become separate conversations with the user. A browser or
other client can reconnect to the server without taking ownership of the
conversation history.

The repository includes [`starter/`](starter/), a small desktop application
with a chat interface. It is a working example and a template you can edit,
not a required interface for every application.

## Try the starter

You need Python 3.12+, [`uv`](https://docs.astral.sh/uv/), Node.js/npm and the
system libraries needed by Playwright Chromium. From this repository's root:

```sh
uv sync
npm --prefix starter/ui ci
npm --prefix starter/ui run build
uv run playwright install chromium
uv run python -m starter
```

The window and settings can open without model credentials; generating a reply
requires an account usable by `codex-backend-sdk`. The
[starter guide](starter/README.md) explains sign-in, local data, available
features and platform-specific browser setup. The starter serves its interface
locally; do not expose its port to the public internet.

Once you have tried it, `uv run the-framework bootstrap` copies the starter into
an editable code directory and asks for a *different* directory for private
application data. Both locations must be empty. The command also accepts
`--code-dir` and `--data-dir`. It copies files; it does not build or launch the
new application. Follow the [starter guide](starter/README.md) from the copied
code directory for those steps.

## Build with the library

Applications declare their main agent and other parts in one place. For
example, this declaration names a durable main agent:

```python
from the_framework import AgentApplication, AgentSpec, SessionPolicy

application = AgentApplication(
    name="My Agent",
    version="0.1",
    primary_agent=AgentSpec(
        name="assistant",
        description="My application's conversational agent.",
        session=SessionPolicy.durable(),
    ),
)
```

This declaration names the agent but does not launch a server. Add security,
plugins, services and clients as your application requires. The
[framework guide](docs/framework.md) explains those pieces; the
[minimal application](examples/minimal_agent_app.py) shows a complete HTTP
service. The Python import is `the_framework`; the distribution and bootstrap
command are named `the-framework`.

## Work on the framework

The distribution is currently installed from this source checkout; it is not
published to a package index. The reusable library is in
[`the_framework/`](the_framework/), the editable example in
[`starter/`](starter/), and their tests in [`tests/`](tests/).

```sh
uv sync --extra dev
uv run pytest -q tests/application tests/agentic
```

Tests under `tests/application/` check behavior visible to an application,
including a build of the wheel outside the source tree. Tests under
`tests/agentic/` probe the framework's evolving internals. Changes to the
starter interface also need its npm tests and build; see its guide.
