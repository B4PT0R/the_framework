# The Framework

This repository is a Python toolkit for building applications around a
long-running AI agent. The toolkit handles the agent's conversation and tools,
the server that connects it to clients, and the worker processes that keep
long-running work separate from the server. An application supplies its own
identity, instructions, plugins and user interface.

There are two main parts:

- [`core/`](core/) is the reusable library. Its Python import name is `core`;
  the installable distribution and bootstrap command are named `harness-core`.
- [`starter/`](starter/) is a small working application built only with that
  library. You can copy it and change its behavior and interface without
  modifying the framework. Its [README](starter/README.md) explains how to run
  it and what is included.

[The Harness](https://github.com/B4PT0R/the_harness) is a separate application that uses this
framework for Pandora. It is not required to use the starter or import `core`.
This framework has not yet been published to a package index; The Harness uses
this checkout as a local dependency.

## Try it locally

From this repository's root, with Python 3.12+ and
[`uv`](https://docs.astral.sh/uv/) installed:

```sh
uv sync --extra dev
uv run pytest -q tests/application tests/agentic
uv run harness-core bootstrap
```

The last command asks for two separate, empty locations: one for a copy of the
starter's editable code and one for its private data (conversation, settings and
files). It does not start the application. To build and launch the copied
starter, follow its [setup instructions](starter/README.md). You may also pass
`--code-dir` and `--data-dir` to bootstrap instead of answering prompts.

To assemble a different application directly with the library, see the
[framework guide](docs/core-framework.md) and the
[minimal Python example](examples/minimal_agent_app.py).
