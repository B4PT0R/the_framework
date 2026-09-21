# The Framework

Build an agent application with a persistent conversation, tools, plugins and
an authenticated web interface. The Python library handles conversation state,
background worker processes and client connections; you decide what your agent
does and how its interface looks.

You can start in either of two ways:

- Copy the working [`starter/`](starter/) application and adapt its agent and
  interface. Its [guide](starter/README.md) covers setup and everyday use.
- Compose an application from the [`core/`](core/) library. See the
  [framework guide](docs/core-framework.md) and the
  [minimal Python example](examples/minimal_agent_app.py).

## Create an application from the starter

From this checkout, install Python 3.12+ and
[`uv`](https://docs.astral.sh/uv/), then run:

```sh
uv sync
uv run harness-core bootstrap
```

Bootstrap asks for two separate, empty locations: one for your editable
application code and one for private data such as the conversation and uploaded
files. It copies the starter but does not launch it. You may pass `--code-dir`
and `--data-dir` instead of answering prompts. Follow the
[starter setup guide](starter/README.md) to build the interface and run the app.

The Python import is `core`; the distribution and bootstrap command are named
`harness-core`. The distribution is not yet published to a package index, so
install it from this checkout.

## Develop the framework

```sh
uv sync --extra dev
uv run pytest -q tests/application tests/agentic
```
