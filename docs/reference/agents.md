# Agents, turns and private specialists

An `AgentSpec` is a reusable construction recipe. An `Agent` executes turns
against one `Session`. A hosted application gives that agent a worker process;
a standalone agent can run in the calling process without a server or UI.

## Execute a complete turn without a model account

The following is one executable Python program. The fake client supplies two
Responses stream results: a function call, then an answer. The Framework still
constructs the tool schema, validates and executes the call, records its result,
prepares the second request and emits its normal lifecycle events.

```python
import json
from types import SimpleNamespace

from the_framework import AgentExtension, AgentResources, AgentSpec, tool
from the_framework.agent import Message


class Inventory(AgentExtension):
    name = "inventory"
    description = "Read warehouse stock."

    @tool
    def stock(self, sku: str):
        """Return stock for one product SKU."""
        if sku != "MUG":
            raise ValueError("unknown product")
        return {"sku": sku, "available": 12}


class ScriptedResponses:
    def __init__(self):
        self.requests = []

    def create(self, **payload):
        self.requests.append(payload)
        if len(self.requests) == 1:
            item = {
                "type": "function_call", "id": "lookup", "call_id": "lookup-call",
                "namespace": "inventory", "name": "stock",
                "arguments": json.dumps({
                    "sku": "MUG",
                    "user_feedback": "I am checking how many mugs are available.",
                }),
            }
        else:
            assert len(self.requests) == 2
            receipts = [i for i in payload["input"] if i.get("type") == "function_call_output"]
            assert json.loads(receipts[-1]["output"])["status"] == "success"
            assert "available" in json.dumps(payload["input"])
            item = {
                "type": "message", "id": "answer", "role": "assistant",
                "content": [{"type": "output_text", "text": "12 mugs are available."}],
            }
        yield {"type": "response.output_item.done", "item": item}
        yield {"type": "response.completed", "response": {"id": "script", "model": "test"}}


responses = ScriptedResponses()
spec = AgentSpec(
    name="warehouse",
    instructions="Check inventory before answering stock questions.",
    extensions=(Inventory,),
    configuration={"model": "test"},
)
agent = spec.build_agent(resources=AgentResources(
    client=SimpleNamespace(responses=responses),
))
events = list(agent.stream("How many mugs are available?"))
assert len(responses.requests) == 2
assert events[0].type == "agent.turn.start"
assert events[-1].type == "agent.turn.end"
assert sum(e.type == "agent.tool_call.start" for e in events) == 1
answers = [i for i in agent.session.history if isinstance(i, Message) and i.role == "assistant"]
assert answers[-1].content[0].text == "12 mugs are available."
```

This is not a simulated tool invocation: only inference is scripted. It does not
test backend authentication, model tool selection, token deltas or networking.
For a real backend, omit the explicit `resources` and select a model your account
supports. The default client is created lazily and authenticates noninteractively;
constructing a recipe or agent does not initiate login.

The hosted [Notebook example](../../examples/notebook/README.md) goes further:
it crosses real worker subprocesses and the application bridge, persists SQLite
data and reads that data through authenticated HTTP.

## Read a stream correctly

| API | Behavior |
| --- | --- |
| `agent.stream(prompt)` | Synchronous iterator of typed events; producer runs in a daemon thread. Exhaust it to observe completion/errors. |
| `agent.astream(prompt, **turn_options)` | Async iterator; owns and awaits its producer. Options go to `AgenticLoop.turn`. |
| `agent.on(event_type, callback)` | Subscribe to one event type, or `"*"`. Callbacks execute synchronously in the emitting thread; async callbacks are not awaited. |
| `agent.add_message(role, text, content=...)` | Append a canonical message and emit `agent.response_item.added`; does not start generation. |
| `agent.interrupt(reason)` | Interrupt the active loop; not a rollback of executed tools or external effects. |
| `agent.end_turn()` | Finish the current tool-call batch, then stop before another model step. |
| `agent.compact()` | Perform canonical context compaction; see [Context](context.md). |

Do not execute concurrent turns on the same agent. Hosted workers serialize their
commands. A standalone caller must provide its own serialization and lifecycle.
Async consumers that might break early should explicitly close their iterator:

```python
import asyncio
from contextlib import aclosing

async def consume():
    other = AgentSpec(name="async-example").build_agent(
        resources=AgentResources(client=SimpleNamespace(responses=ScriptedResponses())),
    )
    other.add_extension(Inventory)
    async with aclosing(other.astream("Check MUG stock.")) as stream:
        async for event in stream:
            if event.type == "agent.turn.end":
                break
    return other

async_agent = asyncio.run(consume())
assert async_agent.session.history
```

Closing an async stream cancels and awaits its producer, but cancellation cannot
forcibly stop Python code already running in a synchronous thread. Closing a
synchronous iterator disconnects its queue; it does not stop its producer thread.
Always distinguish provisional text deltas from committed items; client rendering
and event fields are specified in [Events](events.md).

A normal turn first checks slash commands. A recognized command bypasses inference.
Otherwise it appends the prompt, prepares history and providers, generates output,
executes local tool calls **sequentially in returned order**, then generates again
if needed. `parallel_tool_calls` is a backend request option, not a promise of
concurrent Python tool execution. A generation with no local calls normally ends
the turn. Checkpoints can admit queued worker controls or request compaction.

## Recipe fields and construction order

| `AgentSpec` field | Purpose |
| --- | --- |
| `name`, `description` | Identity and catalogue description; nonblank. A session cannot be rebuilt with a different recipe identity. |
| `instructions` | A string, or sequence of `Instruction`/instruction mappings. A string becomes one instruction named `role`. |
| `configuration` | Root model/context settings and extension settings under their binding names. |
| `extensions` | Extension classes, instances, factories, `module:attribute` references or plugin contributions. Use factories/classes for fresh agents. |
| `resources` | Callable receiving a workspace `Path` or `None`, returning `AgentResources`. Not a coroutine or a storage-file factory. |
| `initializers` | Callables run after extensions load; return the agent or `None`. |
| `command_middleware` | Async-generator middleware used by `build_worker`, not direct `agent.stream`. Contract: `(agent, command, call_next)`. |
| `projections` | Named pure archive projections; `display`, `activity` and `full` are reserved. |

Configuration precedence, lowest to highest:

1. `AgentSpec.configuration`;
2. `AgentResources.configuration`;
3. resolved worker launch profile (advanced process-boundary input);
4. configuration already saved in the `Session`.

Explicit `build_agent(resources=...)` replaces the resources factory entirely.
`AgentResources.client` is process-local, not serialized into the session.
Its `state` supplies initial state; saved session state wins. To obtain a workspace
without disk persistence, pass `session_root=Path(...)` with an in-memory session.
For a disk session, the default workspace is the parent of its file.

Root settings are declared in
[`AgentConfig`](../../the_framework/agent/models/config.py). They include model,
reasoning, text format/verbosity, tool selection, maximum image/tool-output budgets
and nested compaction settings. The library, starter and specialist profile
compiler currently have **different model defaults**. Set models explicitly for
your backend; a default string is not a guarantee that an account supports it.
[Extensions](extensions.md) covers validated settings/state changes and hooks.

## Choose persistence independently from task context

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from the_framework import Session

with TemporaryDirectory() as directory:
    path = Path(directory) / "session.json"
    saved = spec.build_agent(Session.open(path), resources=AgentResources(client=object()))
    saved.update_config({"model": "my-selected-model"})
    reopened = spec.build_agent(Session.open(path), resources=AgentResources(client=object()))
    assert reopened.configs.model == "my-selected-model"
    assert reopened.session.agent_name == "warehouse"
```

`Session()` is memory-backed; `Session.open(path)` loads or creates disk-backed
data. `retain_history=False` selects task-local context independently of storage:
a durable specialist can retain its configuration while clearing task context.
The worker enforces task boundaries; direct `agent.stream` is not a worker task.
Saved policy wins on worker resume. `Session.open(path, retain_history=...)` is
an explicit low-level change; save it to commit it. Hosted `open_session` rejects
an explicit conflicting retention policy on resume. See [Sessions](sessions.md)
for locks, archives and independent application sessions.

## Declare and request a private specialist

A specialist has its own recipe, session and worker. Its caller owns the result
handler; the specialist never writes directly into the caller's history.
The following extension can be installed with `AgentSpec.extensions` or as a
plugin's `agent_extension`:

```python
from the_framework import agent_trigger
from the_framework.agent import AgentResult, QueuePolicy

review_spec = AgentSpec(
    name="reviewer",
    description="Review a proposed inventory change for missing evidence.",
    instructions="Return a short review distinguishing facts from assumptions.",
    configuration={"model": "your-supported-model"},
)

class Reviews(AgentExtension):
    name = "reviews"

    @tool
    def request_review(self, proposal: str):
        """Queue a private review; the result arrives later."""
        request_id = self.agent.registry("agents").submit("reviews.reviewer", proposal)
        return {"request_id": request_id, "status": "queued"}

    @agent_trigger(
        review_spec,
        queue=QueuePolicy.fifo(limit=4, backlog_limit=32),
        durable=True,
        retain_history=False,
        completion_retry_limit=0,
    )
    def reviewer(self, result):
        """Deliver a completed review into the caller's canonical history."""
        result = AgentResult.from_dict(result)
        text = result.output if result.status == "completed" else f"Review failed: {result.error}"
        self.agent.add_message("developer", text, kind="review_result")

# Test declaration, request routing and result handling without launching inference.
caller = AgentSpec(name="caller", extensions=(Reviews,)).build_agent()
requested = []
caller.on("agent.task.requested", requested.append)
reviews = caller.extension("reviews")
queued = reviews.request_review("Reduce the reorder threshold to 5.")
request_id = queued["request_id"]
assert requested[0].profile == "reviews.reviewer"
assert requested[0].request_id == request_id
caller.registry("agents").dispatch(dict(AgentResult(
    id="result-1", task_id=request_id, profile="reviews.reviewer", status="completed",
    watermark=caller.session.watermark(), output="Demand evidence is missing.",
)))
assert caller.session.history[-1].kind == "review_result"
```

This last test exercises submission/dispatch **in process**. A standalone agent
has no fleet: nothing consumes the request unless you provide a host. In an
`Application`, the worker publishes the specialist catalogue and the session's
fleet launches/routes private work. Model execution and result delivery are async;
`submit` returns an ID, not the eventual answer. The handler above appends an
ordered observation; it does not itself ask the caller model for another turn.

### Invocation policies

| Trigger option | Meaning |
| --- | --- |
| `durable=False` | Default: private conversation in memory. `True` persists it across worker restarts. |
| `retain_history=False` | Default: clear private context between tasks. `True` retains it. Independent of durability. |
| `queue` | `QueuePolicy.fifo()` defaults to limit 8, durable task journaling and backlog 512. `latest()` defaults to limit 1, nondurable, backlog equal to limit. |
| `idle_timeout_seconds` | Positive optional worker idle lifetime. |
| `completion_tool` | Require a named tool before accepting completion. |
| `completion_retry_limit` | Additional attempts; zero permits the original attempt only. Nonzero requires a completion tool. |
| `activity_visible` | Hide/show transient client progress. Does not disable tasks, results or diagnostics. |
| `observes`, `item_kinds`, `conversation_roles`, `input_item_field` | Filter ambient observations and optionally extract input items. Explicit named requests bypass ambient filters. |

Trigger policies belong to `AgentTrigger`, not `AgentSpec`. A trigger instance can
replace its `spec` at load time without mutating the class declaration. Explicit
`Plugin.specialists` accepts `AgentTrigger`; a bare `AgentSpec` uses defaults.
These declarations augment class-discovered triggers. Inactive bindings remain in
the configured catalogue so the host can suspend/re-enable them.

`WorkerProfile` is a serializable process-boundary payload, not an alternative
recipe API. `AgentTrigger.profile()` compiles invocation policy; Python resource
factories, initializers and projections are resolved from the application module,
not reconstructed from JSON. Do not cache a second compiled profile catalogue.

### Failure and redelivery

Task journaling, private conversation storage and caller result delivery are
separate contracts. Durable tasks survive temporary startup/transport failures.
Explicit command failure, interruption or exhausted completion attempts are
terminal and enter the result outbox rather than being resubmitted forever.

Terminal results are persisted before entering the durable outbox. A stable
command identity suppresses delivery after a completed receipt; interruption can
cause redelivery. Acknowledgement removes a result only after successful delivery.
Handlers must tolerate **at-least-once** delivery: a crash between an external
side effect and the receipt is not an atomic transaction. Use `result.id` or a
domain idempotency key when your handler writes to another system.

Source: [`spec.py`](../../the_framework/agent/spec.py),
[`agentic_loop.py`](../../the_framework/agent/runtime/agentic_loop.py),
[`specialists.py`](../../the_framework/agent/extensions/specialists.py).
