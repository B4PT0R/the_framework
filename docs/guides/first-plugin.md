# Build a persistent Notebook feature

Build one feature across the worker and server: the agent saves notes, `/project`
selects a project, the UI can read the same notes over HTTP, and each conversation
has its own SQLite database. The model receives fresh note titles before each
step, without adding those snapshots to conversation history.

The complete implementation is in [`examples/notebook/`](../../examples/notebook/).
This chapter explains how to install it and follows a save through each boundary.
It assumes a copied starter from [Your first application](first-application.md).

## 1. Copy the feature into the application

From a Framework source checkout, copy `examples/notebook/` into the `starter/`
package of your editable application. The resulting files are:

```text
my-app/
  starter/
    application.py
    notebook/
      __init__.py
      models.py
      agent.py
      server.py
```

The example directory is not part of the installed Framework package. If you
installed the wheel, obtain these files from the matching source revision rather
than attempting `import the_framework.examples`.

In `starter/application.py`, import the declaration:

```python
from .notebook import NOTEBOOK
```

Add `NOTEBOOK` to the existing `Application.plugins` tuple. Keep the other
entries you need. Do not create another global application or replace the
starter's access policy. Stop and restart the application using its existing
launcher; a new installed service changes startup composition.

The declaration in `notebook/__init__.py` is:

```python
from the_framework import Capability, Plugin, ServerExtension
from .agent import NotebookExtension

NOTEBOOK = Plugin(
    name="notebook",
    agent_extension=NotebookExtension,
    provides_capabilities=(Capability(name="example.notebook"),),
    server_extensions=(ServerExtension(
        name="notebook_store",
        scope="session",
        service_factory=f"{__name__}.server:NotebookService",
    ),),
)
```

`__name__` makes the server import reference work both as `examples.notebook`
and after copying it to `starter.notebook`. The worker imports the agent half;
the server resolves `NotebookService` and constructs it once per open session.
The declared capability advertises the feature contract (version 1 by default).
It is not an authorization grant or the bridge registration itself.

## 2. Choose what persists, and where

There are three independent records:

| Record | Owner | Stored value |
| --- | --- | --- |
| Configuration | Canonical session | `notebook.recent_limit`, a user setting |
| Plugin state | Canonical session | `notebook.project`, changed during use |
| Notes | Session-scoped server service | SQLite rows keyed by `(project, key)` |

The schemas in [`models.py`](../../examples/notebook/models.py) reject unknown
fields, unsafe identifiers and excessive payload sizes. `SaveNote` requires a
nonblank title of at most 200 characters and a body of at most 20,000 characters.
`RecentNotes` requires a project and a limit between 1 and 20. Identifiers use
1–64 ASCII letters, digits, hyphens or underscores.

Both HTTP and the worker bridge construct these same models. Type annotations
on a tool describe its schema to the model; they do not replace validation at
the storage boundary.

The worker extension declares separate configuration and state classes:

```python
from modict import modict
from the_framework import AgentExtension, command, provider, tool
from the_framework.agent import ApplicationError, Config, State
from .models import identifier

class NotebookConfig(Config):
    recent_limit: int = 5

    @modict.validator("recent_limit", mode="after")
    def bounded_limit(self, value):
        if not 1 <= value <= 20:
            raise ValueError("recent_limit must be between 1 and 20")
        return value

class NotebookState(State):
    project: str = "inbox"

    @modict.validator("project", mode="after")
    def valid_project(self, value):
        return identifier(value)

class NotebookExtension(AgentExtension):
    name = "notebook"
    config = NotebookConfig
    state_schema = NotebookState
    instructions = (
        "Use notebook.save_note to store durable notes under stable keys. "
        "Saving the same project/key replaces that note. The current project "
        "comes from /project; do not invent a successful save when storage fails. "
        "Recent note previews contain only titles, not complete note bodies."
    )

    @command
    def project(self, name: str):
        """Select the notebook project: /project research."""
        self.persist_state(project=name)
        return {"project": self.plugin_state.project}

    @tool
    async def save_note(self, key: str, title: str, body: str):
        """Save or replace one note in the current project under a stable key."""
        return await self.agent.bridge.call("notebook", "save", {
            "project": self.plugin_state.project,
            "key": key, "title": title, "body": body,
        })

    @tool
    async def recent_notes(self, limit: int = 5):
        """Read the most recently saved notes, including their complete bodies."""
        return await self.agent.bridge.call("notebook", "recent", {
            "project": self.plugin_state.project, "limit": limit,
        })

    @provider(channels=("text",))
    async def notebook_context(self):
        """Current project and recent note titles, refreshed before every step."""
        project = self.plugin_state.project
        try:
            result = await self.agent.bridge.call("notebook", "recent", {
                "project": project, "limit": self.config.recent_limit,
            })
        except ApplicationError as error:
            return {"project": project, "status": "unavailable", "error": str(error)}
        return {"project": project, "status": "ready", "notes": [
            {"key": note["key"], "title": note["title"]}
            for note in result["notes"]
        ]}
```

The slash command runs without inference. `/project research` validates and
commits the new state; `/project ../bad` returns a failure and leaves the previous
project unchanged. Use `persist_state`, not a bare assignment to `plugin_state`,
for changes that must survive reconstruction and reach clients.

A model call to `notebook.save_note` includes `key`, `title`, `body` and the
Framework-required `user_feedback`. The Framework consumes that feedback field;
it is not passed to the Python method. The tool awaits the bridge, rather than
opening the database inside the worker.

## 3. Construct the server resource

[`server.py`](../../examples/notebook/server.py) contains the complete service.
Its constructor receives dependencies by parameter name:

```python
class NotebookService:
    def __init__(self, context, runtime):
        self.path = context.require("session_path").parent / "notebook.sqlite3"
        self.lock = RLock()
        self.database = None
        runtime.bridge.register("notebook", self.handle)
```

`context` is the session's `BuildContext`; `runtime` is that session's
`SessionRuntime`. The default conversation uses the database beside its session
file; additional conversations use their own session directories. A project
selects rows inside that database. It is not a filesystem path or an access rule.

`start()` opens the connection, creates the table and closes the connection if
initialization fails. `stop()` closes it under the same lock used by queries.
The connection uses `check_same_thread=False` because HTTP and bridge work can
run on different threads. The lock serializes operations; the connection context
commits each write or rolls it back on failure. Startup ordering and reverse
shutdown belong to the Framework's service graph.

The two HTTP methods are ordinary service methods with explicit request models:

```python
@endpoint("put", "/api/v1/notebook/notes", request=SaveNote)
def save(self, project, key, title, body):
    note = SaveNote(project=project, key=key, title=title, body=body)
    with self.lock, self.database:
        revision = self.database.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM notes"
        ).fetchone()[0]
        self.database.execute("""
            INSERT INTO notes VALUES (:project, :key, :title, :body, :revision)
            ON CONFLICT(project, key) DO UPDATE SET
                title=excluded.title, body=excluded.body, revision=excluded.revision
        """, {**note, "revision": revision})
    return {"project": project, "key": key, "revision": revision}
```

For `PUT`, the adapter reads a JSON body; for `GET`, it reads query parameters.
Authentication is required by default. The bridge handler is explicit too:

```python
async def handle(self, method, payload):
    if method == "save":
        values, handler = SaveNote(**payload), self.save
    elif method == "recent":
        values, handler = RecentNotes(**payload), self.recent
    else:
        raise ValueError(f"unknown notebook operation: {method}")
    return await asyncio.to_thread(handler, **values)
```

It accepts only two operations, validates before dispatch and moves SQLite work
off the server event loop. Registering a bridge handler does not automatically
expose every method on a service.

## 4. Exercise the feature

In the application's chat, send `/project research`, then ask it to save a note
under key `architecture` with a title and body. Inspect the actual tool result.
Saving that project/key again replaces its content and increases the revision;
`recent_notes` returns complete bodies, while the provider supplies titles only.

Inside the authenticated starter interface, the equivalent reads use its
same-origin session credentials:

```javascript
const response = await fetch(
  "/api/v1/notebook/notes?project=research&limit=5",
  { credentials: "same-origin" },
);
if (!response.ok) throw new Error(`Notebook HTTP ${response.status}`);
const { notes } = await response.json();
console.table(notes);
```

For another open conversation, prefix the route with `/sessions/<session-id>`.
The unprefixed route addresses the default session; changing a frontend's selected
conversation does not implicitly change a URL. Requests outside the authenticated
interface need the credentials supported by your own access policy. Do not put
a bearer token in a query string or hard-code one in frontend code.

Disabling the Notebook binding removes its tools, command, instructions and
provider from that agent. It does not stop `notebook_store`, remove the HTTP
routes or erase notes. Removing the installed plugin requires new startup
composition and a restart.

## 5. Verify without a model account

From the Framework checkout:

```sh
python -m pytest -q tests/general/test_documented_notebook.py
```

The tests cover storage reopening/upserts, invalid inputs, command failure
without state mutation, reconstructed configuration/state, tool output decoding,
provider channel filtering and authenticated HTTP validation. The hosted test
starts two real worker subprocesses, feeds a deterministic Responses stream,
executes a save through the worker/server bridge, reads it over HTTP and checks
session isolation and clean shutdown. No inference request is made.

The deterministic backend in
[`tests/fixtures/notebook_app.py`](../../tests/fixtures/notebook_app.py) proves the
Framework path, not a model's ability to choose the right operation. Testing that
behavior requires a separately configured backend and explicit inference runs.

## Next changes

- Add pagination or full-text search in the storage contract, then expose it to
  HTTP and bridge callers together.
- Add a UI view using the existing authenticated routes; do not add a second
  database connection in the client.
- Add a read-only specialist only if the feature needs a separate agent/session.
  A tool calling an ordinary service does not need one.

See [tools and results](../reference/tools.md),
[extension state and lifecycle](../reference/extensions.md),
[providers](../reference/providers.md) and [services](../reference/services.md)
for contracts beyond this example.
