# Sessions and retained history

`Session` is canonical data, not a worker, server controller or model context.
It contains the active history, retained archive, agent configuration and state,
plugin bindings, command receipts and context bookkeeping. `SessionRuntime`
hosts execution for one session; see [Hosting](hosting.md).

## Open, mutate and reopen a file-backed session

This complete program needs no backend or server. `Session.open()` reads a file
if it exists; it does not create a worker or acquire a writer lease. Mutations
below save a snapshot. `path` is runtime metadata, not a serialized field.

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from the_framework.agent import InputText, Message, Session
from the_framework.agent.context.projections import SessionProjections

with TemporaryDirectory() as directory:
    path = Path(directory) / "session.json"
    with Session.writer(path):
        session = Session.open(path)
        assert not path.exists()
        question = Message(role="user", content=[InputText(text="Keep the red design.")])
        session.append(question)
        session.append(Message(role="assistant", content=[InputText(text="Red retained.")]))
        session.update_agent(state={"design": {"color": "red"}})
        session.set_plugin("design", True)
        session.set_command("choose-red", "completed")

    restored = Session.open(path)
    assert restored.id == session.id
    assert restored.state["design"]["color"] == "red"
    assert restored.command_status("choose-red") == "completed"
    assert "path" not in restored
    assert restored.watermark().revision == 2
    page = restored.page_archive_turns(limit=1)
    assert page["turns"][0][0].id == question.id
    assert not page["has_more"]
    display = SessionProjections().project("display", page["turns"][0])
    assert [item.role for item in display] == ["user", "assistant"]
```

Use `Session()` for memory-only storage. File persistence and history retention
are independent: a durable session can save settings and command receipts while
clearing conversational context between worker tasks.

## Mutation and ownership contract

| Method | Effect |
| --- | --- |
| `append(item)` | Adds to active history and archive; increments revision. Repeated artifact ids and unavailable local image/copied-file references are not appended |
| `replace(history, anchor_id=None)` | Replaces active history, not archive; updates context watermark and clears usage |
| `commit_compaction(...)` | Atomically installs compacted history and retained archive; see [Context](context.md) |
| `update_agent(configuration=..., state=...)` | Replaces supplied JSON-compatible mappings; omitted mappings stay unchanged |
| `set_plugin(name, activated)` | Saves an agent binding, not server runtime installation |
| `set_command(id, status, error=None, deferred=None)` | Saves a command receipt independently of history revision |
| `set_context_usage(usage)` | Saves backend usage and cumulative cache observations |
| `clear_context()` | Clears history/archive/anchors/usage, not settings, bindings or command receipts |
| `sanitize()` | Repairs interrupted tool protocol and prunes missing local references |
| `recover_commands()` | Marks unfinished ordinary commands interrupted; retains deferred work as waiting |

Supported mutation methods serialize their mutation and snapshot under an
in-process reentrant lock and restore affected in-memory fields when saving
fails. Direct list/dict edits bypass those guarantees. Snapshot replacement uses
`utils.persistence.atomic_text_writer`; it is neither a multi-file transaction
nor a transaction with external tool effects. Directory-sync failure after
replacement is logged as uncertain durability, not rolled back.

`Session.writer(path)` is the worker ownership lease: a nonblocking Unix `flock`
on the sibling `.lock` file, held until context exit. A second owner raises
`RuntimeError`. Hosted worker startup acquires this lease, then opens the session.
`Session.open()` by itself does not prevent two independent objects from
replacing the same file; never use independent writers to one canonical session.

## Persistence versus retention

- `Session.retain_history` is persisted and defaults to true. Agent construction
  clears old context when it is false; the worker also clears context after a
  turn request, including failure cleanup. This is a worker-task policy, not an
  automatic cleanup after every direct standalone `Agent.stream()` call.
- Hosted resume uses the saved retention policy rather than launch defaults.
  A conflicting explicit policy on `open_session()` is rejected. The low-level
  `Session.open(path, retain_history=...)` can override the loaded value in memory;
  this is not the hosted resume contract.
- `durable=False` makes a hosted worker use a memory-only session. Its services
  may still own files; resident execution is not a promise of zero disk writes.
- Retention and persistence do not make a command exactly-once in an external
  system. Inspect effects before retrying an interrupted command.

## Archive pages and projections

`history` is the active model working record. `archive` is the retained source
for client history, not an unlimited audit log. Compaction can trim it according
to `compaction.archive_anchor_limit`. Archive turns start at user messages;
repeated item ids are deduplicated for paging. `page_archive_turns(before=None,
limit=25)` returns whole turns, `next_before` and `has_more`. Limits are 1–100;
an unknown cursor yields an empty page.

`SessionProjections` provides `full` (all items), `display` (user/assistant
messages and artifacts) and `activity` (display plus function calls). Custom
projections accept a list and must return a list or tuple; standard names cannot
be replaced. These projections are not Responses input and do not recover or
mutate history. The default HTTP `GET /api/v1/session` uses `display`, with
`before` and `limit` query parameters; there is no public projection selector on
that route. Worker page requests can select a registered projection.

## Host multiple independent conversations

An agent application declares selectable recipes in `Application.agents`.
Authenticated session-management endpoints are:

| Endpoint | Result |
| --- | --- |
| `GET /api/v1/sessions` | List known/running sessions |
| `POST /api/v1/sessions` | Open or resume; JSON fields `session_id`, `agent`, `durable` (default true), `retain_history` |
| `DELETE /api/v1/sessions/{id}` | Stop that session; durable data remains |

Opening returns `id`, `agent` and `api`, such as
`/sessions/review/api/v1`. Use that prefix for the session's conversation,
settings and transports. Root `/api/v1/...` routes delegate to the default
session. Python hosts use `await application.open_session(...)` and
`await application.close_session(id)` during the live application lifecycle.

Session ids accept 1–128 ASCII letters, digits, `_` or `-`. Saved `agent_name`
selects the recipe on resume; changing it implicitly is forbidden. Additional
durable sessions live at `data_root/sessions/{id}/session.json`; the default
session uses the configured `session_path`. A known persisted session can resume
lazily through its prefixed routes; an unknown id does not create one implicitly.
Closing even the default runtime is supported; normal application startup opens
the default and shutdown closes all runtimes.

Application-scoped services are shared. Session-scoped services, workers,
fleets, configuration, bindings and event streams remain separate. There is no
global current session. The [Notebook example](examples.md) tests two real worker
processes with independent SQLite stores and canonical histories.

**Source:** `agent/context/session.py`, `agent/context/projections.py`,
`agent/runtime/worker_process.py`, `agent/runtime/worker.py`,
`server/composition/application.py`, `server/api/sessions.py`, `server/api/agent.py`.
