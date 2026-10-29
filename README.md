# The Framework

Python library for building agent applications: tool execution, durable
conversations, private specialists, backend services, authenticated clients and
editable interfaces. The application owns its instructions, permissions and UI;
the library owns their composition and runtime contracts.

**Python 3.12+ · alpha · distribution `b4pt0r-the-framework` · import `the_framework`**

## Run an editable application

With `uv`, Node.js/npm and Chromium's OS dependencies installed:

```sh
python -m pip install b4pt0r-the-framework
the-framework bootstrap --code-dir /path/to/my-agent-code --data-dir /path/to/my-agent-data --run
```

Use absent or empty, separate, non-overlapping directories. Bootstrap copies the
starter, prepares its Python environment and React UI, downloads Playwright
Chromium and launches a local desktop window. Model login is a separate step;
see [First application](docs/guides/first-application.md) for credentials, a first
reply, instruction changes and resuming the same conversation.

The starter includes shell/browser tools with the configured account's real
permissions. It is **not a sandbox**. Keep its listener on loopback and private
data outside source control.

## Compose an application

This is a declaration, not a running server. It gives one recipe shell tools,
a durable registry and artifact presentation. There is no compulsory persona or UI:

```python
from the_framework import AgentSpec, Application
from the_framework.plugins.artifacts import ARTIFACTS
from the_framework.plugins.bash import BASH
from the_framework.plugins.registry import REGISTRY

application = Application(
    name="Workbench",
    version="0.1.0",
    agents=(AgentSpec(
        name="assistant",
        instructions="Inspect existing files before editing; verify changes with tests.",
        configuration={"model": "your-supported-model"},
    ),),
    plugins=(BASH, REGISTRY, ARTIFACTS),
    surfaces=(),  # explicitly headless; omit to use editable ui/ beside the app module
)
plan = application.compile()  # validates composition; starts no workers or services
```

A host calls `application.build(...)` with the importable application reference,
storage paths and access policy, then runs the returned ASGI app. Each independent
conversation has one writer worker and a canonical `Session`. Plugins may add
worker tools, scoped server services/routes and private agents.
[Application](docs/reference/application.md) specifies assembly;
[Hosting](docs/reference/hosting.md) specifies process and bridge boundaries.

## Choose a working path

| Task | Starting point |
| --- | --- |
| Run, authenticate and customize the desktop starter | [First application](docs/guides/first-application.md) |
| Run an agent/tool turn without a server or model account | [Executable agent example](docs/reference/agents.md#execute-a-complete-turn-without-a-model-account) |
| Build a feature with storage, HTTP, tools and live context | [Notebook tutorial](docs/guides/first-plugin.md), [complete source](examples/notebook/README.md) |
| Look up schemas, tool results, commands, state or hooks | [Tools](docs/reference/tools.md), [Extensions](docs/reference/extensions.md) |
| Build a client, integrate voice or operate deployments | [Documentation index](docs/README.md) |
| Understand data/process ownership and restart limits | [Architecture](docs/explanation/architecture.md), [Continuity](docs/explanation/continuity.md) |
| Work on the library itself | [Development](docs/guides/development.md) |

Documentation describes this checkout, not every published wheel. See
[CHANGELOG](CHANGELOG.md) for releases, [security](docs/reference/security.md)
for hosting boundaries and [MIT license](LICENSE) for reuse terms.
