# Notebook feature

A session-scoped feature for the editable starter: SQLite storage, authenticated
HTTP, worker tools, a slash command, typed persistent state and a text-only
context provider. No model credentials are needed to run its tests.

From the Framework checkout, using an interpreter with `.[dev]` installed:

```sh
python -m pytest -q tests/general/test_documented_notebook.py
```

In the five-repository development workspace, use `../.venv/bin/python` instead
of `python`. Do not create another checkout-local environment.

Follow [the tutorial](../../docs/guides/first-plugin.md) to install this feature
in a copied starter. These files are application code, not an extra Framework
plugin distributed with the wheel. Copy the `notebook/` directory into your
application package before importing it there.

| File | Responsibility |
| --- | --- |
| `__init__.py` | Install the agent extension and declare the session service |
| `models.py` | Validate project/key, payload sizes and query limits |
| `agent.py` | Tool calls, `/project`, configuration/state and ephemeral titles |
| `server.py` | SQLite lifecycle, HTTP endpoints and worker bridge handler |

A stable `(project, key)` replaces a note; revisions increase within the database.
Each hosted conversation gets its own database beside its session file. The
current project is state, not a permission boundary. All clients authorized for
that session can use its HTTP routes. This is not a per-user access-control
example.

Tests distinguish direct storage, local tool-pipeline and full hosted-worker
coverage. Their backend is simulated; they do not establish model quality,
account availability or a browser's rendering.
