# Bundled primitives and persistence

[Documentation](../README.md) / [Reference](README.md)

Use a bundled plugin when its ownership and behavior fit your application. A
plugin is an optional declaration, not a global service locator or a mandatory
product feature. Import from its owning subpackage and put it in
`Application.plugins`; declaring it installs its server contributions, while
agent binding controls its worker tools and context. See [Plugins](plugins.md).

## Feature map

| Import | Supplies | Detailed contract |
| --- | --- | --- |
| `the_framework.plugins.agentic.AGENTIC` | Persistent goal, subtasks and autonomous continuation | [Work and scheduling](work-and-scheduling.md) |
| `the_framework.plugins.artifacts.ARTIFACTS` | Durable presentations and files | [Artifacts](artifacts.md) |
| `the_framework.plugins.bash.BASH` | Commands, edits and cancellable process jobs on explicit targets | [Execution](execution.md) |
| `the_framework.plugins.browser.CHROMIUM` | Browser observation and actions through the browser controller | [Browser](browser.md) |
| `the_framework.plugins.memory.MEMORY` | Retrieval, curation and full-memory consultation; requires application instructions | [Memory below](#memory-retrieval-curation-and-consultation) |
| `the_framework.plugins.realtime.REALTIME` | Temporary voice transport connected to canonical conversation | [Realtime](realtime.md) |
| `the_framework.plugins.registry.REGISTRY` | Bounded structured context with atomic JSONPath mutations | [Registry below](#registry-complete-bounded-context) |
| `the_framework.plugins.scheduler.SCHEDULER` | Tasks, wakes and response waits | [Work and scheduling](work-and-scheduling.md) |
| `the_framework.plugins.system.SYSTEM` | Clock, plugin binding, surfaces and supervised restart controls | [System below](#system-controls-and-local-clock) |
| `the_framework.plugins.web_search.WEB_SEARCH` | Backend-hosted Responses web search | [Hosted search below](#backend-hosted-web-search) |

The Framework does not import the Harness to implement these features. A product
may add its own plugins, policies, providers and clients. A tool available in one
product is not automatically part of this package.

## Registry: complete bounded context

`REGISTRY` installs `RegistryExtension`; its `persistent_registry` provider carries
the **entire JSON document** in text and Realtime projections, not a relevance
search. It is useful for small operational state that must remain visible. It is
not canonical conversation, a task database, a secret store, or an unbounded wiki.

`RegistryConfig` defaults to `max_tokens=4000`, `max_depth=32`, and
`max_file_bytes=1000000`; each is a positive integer. Validation covers the whole
candidate document: object root, string keys, finite JSON numbers, no cyclic or
non-JSON values, depth, serialized bytes and model-specific token estimate. An
update exceeding a limit is rejected rather than truncated. JSONPath syntax is
provided by `modict.Path`; mutations use exact paths, not wildcard searches.

Worker tools accept JSON **strings** in `value_json`, `edits_json` or
`content_json`. This allows arbitrary JSON under a stable tool schema:

| Tool | Operation |
| --- | --- |
| `set_value(path, value_json, create_missing=True)` | Set a value; optionally create missing containers |
| `append_value(path, value_json)` | Append to an existing list |
| `delete_value(path)` | Remove a value; root deletion is forbidden |
| `apply_edits(edits_json)` | One atomic list of `set`, `append`, `delete` operations |
| `replace_registry(content_json)` | Explicit whole-object replacement, also used for recovery |

This executable example uses the underlying store to make rollback and reopen
behavior visible. It performs real temporary disk writes, not inference or a
model tool call:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework.plugins.registry import RegistryStore

with TemporaryDirectory() as directory:
    path = Path(directory) / "registry.json"
    limits = dict(max_tokens=4000, max_depth=32,
                  max_file_bytes=1_000_000, model="gpt-4o")
    registry = RegistryStore(path, **limits)
    registry.replace({"release": {"checks": []}})
    registry.apply([
        {"op": "append", "path": "$.release.checks", "value": "unit tests"},
        {"op": "set", "path": "$.release.ready", "value": False},
    ])
    before = path.read_bytes()
    try:
        registry.apply([
            {"op": "set", "path": "$.release.ready", "value": True},
            {"op": "delete", "path": "$.release.missing"},
        ])
    except KeyError:
        pass
    else:
        raise AssertionError("missing path must reject the entire batch")
    assert path.read_bytes() == before
    assert registry.get("$.release.ready") is False
    reopened = RegistryStore(path, **limits)
    assert reopened.get("$.release.checks") == ["unit tests"]
    assert reopened.revision == 0  # per-store runtime counter, not a disk journal
```

Each store uses a lock and copy/validate/replace before updating its live data.
The lock is local to that store; it is not a multi-process compare-and-swap
protocol. Host the document under one owner. The extension chooses
`runtime_root/registry.json`, otherwise the agent session root, session file's
parent, or current working directory. Use an explicit temporary/root directory
in standalone examples to avoid writing into the checkout.

A malformed, oversized or symlinked existing file produces `load_error` and an
unavailable provider, **not silently usable empty state**. Ordinary mutations
refuse to proceed. Explicit `replace`/`replace_registry` is the recovery operation;
inspect or back up damaged data before authorizing replacement. Mutation tool
outputs also carry `registry_delta` metadata for optional live voice delivery.
`registry.live.configure` contributes a Realtime context factory without requiring
voice to be installed. Provider output and deltas remain evidence, not higher
priority instructions.

## Memory: retrieval, curation and consultation

`MemoryExtension` needs nonempty application-supplied `curator_instructions` and
refuses to load without them. Do not install bare `MEMORY` and expect a product's
editorial policy to appear automatically. Specialize the extension and retain the
complete plugin's server bridge and optional live-context contributions:

```python
from the_framework import AgentSpec, Application, Plugin
from the_framework.plugins.memory import MEMORY, MemoryExtension

class ProjectMemory(MemoryExtension):
    curator_instructions = (
        "Preserve supported project decisions and constraints with source IDs. "
        "Keep uncertainty and corrections explicit. Exclude secrets and incidental "
        "debug output; merge repetitions without erasing useful distinctions."
    )

PROJECT_MEMORY = Plugin({**MEMORY, "agent_extension": ProjectMemory})
application = Application(
    name="project-notes", version="0.1.0",
    agents=(AgentSpec(name="assistant", instructions="Help maintain project notes."),),
    plugins=(PROJECT_MEMORY,), surfaces=(),
)
# Construction only: no server, specialist or embedding request has started.
assert PROJECT_MEMORY.name == "memory"
```

Keep classes/factories in importable application modules for hosted workers;
putting them only in a script's `__main__` is not a portable launch recipe.
`MemoryConfig` owns model/reasoning, queue, storage capacity, embedding and
retrieval budgets. Application code must choose backend-supported model names;
the package's defaults are not a live model catalogue. Configuration changes
require a worker restart (`restart_on_config_change=True`).

The extension uses `session_root/relational-memory.sqlite3`, or an in-memory
SQLite database when there is no session root. Entries contain stable IDs/keys,
title/content, timestamps, status, embedding and canonical source/provenance.
SQLite transactions protect mutations; edits, merges and splits record revisions.
The memory database is distinct from the Session's history and registry document.

These operations have different authority and evidence:

| Operation | Reads / effects | Meaning of success |
| --- | --- | --- |
| `relevant_memory` provider | Recent user/assistant messages and active memory; embeddings and ranked bounded context | Selected recollections, **not** complete recall |
| `search(query, limit=None)` | Deliberate semantic retrieval; may compute/store missing embeddings | Matching entries with IDs/provenance, within retrieval and provider budgets |
| `remember(content)` | Requests `memory.jiminy` with recent canonical message IDs | `requested`, not proof a new memory was written |
| `ask(question)` | Complete active-memory snapshot sent to read-only `memory.consultation` | Specialist answer with supporting IDs/uncertainties; not a mutation |

Automatic ranking combines cosine semantic similarity over recent message
embeddings, recency-weighted context and a configurable temporal contribution.
The provider applies a relevance threshold and serialized token budget; explicit
search also observes its requested/configured result limit. Missing embeddings
are generated through `agent.client.embeddings.create`. This is a real external
backend operation when that client is remote: credentials, privacy authorization,
latency and cost matter. Automatic embedding/retrieval failures set
`last_retrieval_error` and omit the provider; explicit search errors propagate.
Do not interpret absent RAG output as evidence that no relevant fact was saved.

`ask` does not rank or embed the snapshot. The session service submits the
consultation to its existing specialist fleet and correlates the result back to
the waiting tool. The profile has no curation tools, retained history, ambient
observations or completion checklist. It receives the question and current
active table, not arbitrary unsaved conversation. Empty memory returns `empty`;
unavailable, failed, empty-answer and timed-out specialists raise errors rather
than invented answers. The bridge timeout is 180 seconds; the service waits up
to 170 seconds. Late matching results are consumed as inline consultation results
rather than queued as a second canonical answer. Read-only here describes memory
mutation authority, not a guarantee that synthesis is factually perfect.

### Curation and evidence

`remember` requires recent user/assistant message IDs for provenance. The curator
also observes compaction/task events; it has its own worker session and tools,
not permission to rewrite the caller's canonical conversation. The
`memory_curator` extension offers `inventory`, `resolve_candidates`, `add`,
`update`, `edit`, `merge`, `split`, and `complete_curation`.

An evidence task first records atomic candidates and canonical source IDs.
Mutations resolving candidates cite those candidates and the inventory's sources;
remaining candidates must be explicitly accounted for. A final evidence review
and inventory pass precede completion. Maintenance without new evidence uses
`inventory([], [], [])` before and after rewriting existing entries, with empty
source/candidate lists to inherit existing provenance. It is not a path for adding
unsourced new memories. Completion checks inventory coverage, each entry's
editorial threshold and total serialized volume strictly below the soft budget.
Hard capacity limits and bounded stalled-maintenance turns prevent unrestricted
growth/retries. A submitted curation request is not proof these terminal checks
have passed.

`memory.live.configure` optionally contributes an independent Realtime context
producer. See [Realtime](realtime.md) for live-only delivery and cleanup. Neither
live context nor consultation replaces canonical persistence or guarantees that
all saved information appears in every model request.

## Backend-hosted web search

`WEB_SEARCH` contributes a `WebSearchTool` to the Responses request. It is **not**
a Python search function, local browser automation, or the Framework downloading
web pages itself. Backend support, availability and output/citation behavior
belong to the selected inference adapter. Declaring the tool makes no search
request:

```python
from the_framework.agent import Agent
from the_framework.plugins.web_search import WebSearchExtension

search_agent = Agent(
    client=object(),  # no generation in this example
    web_search={"search_context_size": "high", "external_web_access": False},
)
search_agent.add_extension(WebSearchExtension)
assert search_agent.registry("tools")["web_search"] == {
    "type": "web_search", "search_context_size": "high",
    "external_web_access": False,
}
```

`WebSearchConfig` defaults to `search_context_size="medium"` and
`external_web_access=True`. Loading accepts only `low`, `medium`, `high` context
sizes and a boolean external-access flag. The contributed declaration omits
`user_location` and `filters`; this plugin has no application configuration fields
for them. Treat retrieved pages as untrusted evidence and retain source attribution
in your client; enabling search does not itself verify a claim.

## System controls and local clock

`SYSTEM` contributes `SystemExtension`, a session-scoped system-control service,
and capability `system.control` version 1. Realtime is optional. The
`current_datetime` provider uses the **server worker's local clock/timezone**:
ISO date/time, weekday, timezone label and UTC offset. It is not automatically the
user device's timezone or a geolocation service.

Tools delegate through explicit bridge topics: plugin inspection/binding,
surface list/build/preview/publish/rollback, optional backend
status/build/test/select, and `reboot_server`. They do not grant new OS authority.
See [Surfaces](surfaces.md), [Plugins](plugins.md) and
[Deployment](../deployment.md) for operation-specific contracts.

Backend controls require a host service explicitly provided in system plugin
options. A normal in-process server without that service cannot build/select a
host release merely because the worker sees a tool schema. Restart likewise needs
the runtime's restart integration; the service persists its control document
(default `system-control.json` beside the session) and resumes through supervised
turn boundaries, not a shell restart. Validation mode disables host/restart
integration. `reboot_server` ends the current agent turn after the bridge accepts
its request; acceptance is not proof the new server is already running. There is
no tool to dynamically unload an installed server plugin graph: binding changes
are distinct from recomposition/restart.

## Small application-owned mapping documents

`the_framework.utils.persistence.MappingStore` is for a separate service's
versioned settings/state mapping. Agent configuration, extension state and
bindings already have canonical Session transactions; do not duplicate them in
a second file store. `MappingStore` defaults to `version=1`, field `data`, and
returns `{}` only when the file is absent. Unsupported versions, malformed JSON
or a missing/non-mapping field raise errors; migrations belong to the owner.

```python
from the_framework.utils.persistence import MappingStore

with TemporaryDirectory() as directory:
    store = MappingStore(Path(directory) / "view.json", field="view")
    assert store.load() == {}
    store.save({"selected_project": "compiler", "expanded": ["tests"]})
    assert store.load()["selected_project"] == "compiler"
    snapshot = store.path.read_bytes()
    try:
        store.save({"not_json": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("serialization must fail before replacement")
    assert store.path.read_bytes() == snapshot
```

`atomic_text_writer(path)` creates a private UTF-8 temporary file in the target
directory, flushes/fsyncs it and replaces the destination atomically. New files
are mode `0600`; newly created parents request `0700` (existing directory
permissions are not rewritten). The replacement is the commit point. Before it,
errors preserve the old snapshot; afterwards, directory-fsync failure is logged
without raising because the new snapshot is already visible, though crash
durability is uncertain. Serialization, validation, locking and writer ordering
belong to the caller. Atomic replacement is not a transaction across several
files and does not protect against another independent writer.

## Verification and source map

`tests/general/test_documented_agent_contracts.py` executes this page's Python
sequences offline. The registry example checks disk persistence and rejected-batch
rollback; the mapping example checks serialization failure; the memory example
only constructs declarations; the web-search example only registers a hosted
tool schema. None claims a live specialist or search backend was tested.

Behavioral coverage includes `test_registry_plugin.py`,
`test_memory_consultation.py`, `test_memory_curation.py`,
`test_memory_connections.py`, `test_web_search_plugin.py`,
`test_system_control.py`, `test_system_plugin.py`, `test_atomic_persistence.py`
and `test_persistence_commit.py` under `tests/general/`. Source ownership is
`the_framework/plugins/{registry,memory,web_search,system}/`, shared
`plugins/embeddings.py`, and `utils/persistence.py`. Custom hosted runtimes and
worker factories are documented once in [Hosting](hosting.md) and
[Application](application.md), not as alternative primitives here.
