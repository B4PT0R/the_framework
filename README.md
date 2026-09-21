# The Framework

`core` is the reusable Python framework for persistent agent applications.
It provides agent/session runtime, plugin composition, server and worker
supervision, client transports, and browser surfaces without importing any
The Harness product policy. The [starter](starter/README.md) is a small editable
application built entirely on these primitives.

This is a local development checkout; no package has been published.

```sh
uv sync --extra dev
uv run pytest -q tests/application tests/agentic
uv run harness-core bootstrap
```

The bootstrap command prompts for a code directory and a separate private data
directory. It also accepts `--code-dir` and `--data-dir`. See
[the framework guide](docs/core-framework.md) for the composition API.

The Harness consumes this checkout through an editable `uv` path dependency.
Changes to the framework should be tested here before verifying the product.
