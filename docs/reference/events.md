# Events and committed output

An agent emits typed Framework events; a hosted session wraps them in worker
protocol envelopes. Neither is the backend's raw Responses/Live wire stream.
Use events for progress and provisional display, and canonical item commits plus
session pages for durable history.

## Local observation and plugin declarations

This complete example registers a feature-owned event, observes it without a
model request, then serializes it through the same typed command envelope used
by workers:

```python
import json
from the_framework.agent import Agent, Event, LocalEvent
from the_framework.agent.runtime.protocol import CommandEvent

class NotebookIndexed(LocalEvent):
    type: str = "notebook.indexed"
    project: str
    count: int

agent = Agent(client=object())  # no inference is performed
observed = []
agent.on("notebook.indexed", observed.append)
agent.emit(NotebookIndexed(project="demo", count=3))
assert observed[0].count == 3

payload = json.loads(json.dumps(CommandEvent(
    request_id="request-1", command_id="command-1", event=observed[0],
)))
restored = CommandEvent(**payload)
assert isinstance(restored.event, NotebookIndexed)
assert Event.from_dict(payload["event"]).project == "demo"
try:
    Event.from_dict({"type": "response.unregistered_backend_event"})
except ValueError:
    pass
else:
    raise AssertionError("unknown backend events must not cross the boundary")
```

Import plugin event declarations in **every Python process** reconstructing them,
normally through plugin loading. Declare feature events with their owner, not in
the generic worker core. Unknown types are rejected; event models forbid extra
fields. `CommandEvent.event`, `SubagentEvent.event` and `RealtimeEvent.event`
reconstruct declared Framework types rather than accepting arbitrary backend JSON.

`agent.on(type, callback)` and `agent.on("*", callback)` register synchronous
callbacks. They run inline in the emitting thread; they are not awaited, isolated
background tasks or a durable subscription. Exceptions propagate and can fail the
producer. During `agent.stream()` the producer runs in a daemon thread; do not
mutate a browser/event-loop resource directly from its callback. Prefer consuming
`agent.astream()` in asynchronous code; close it explicitly on early exit as
shown in [Agents](agents.md).

## Event vocabulary

| Family | Fields / purpose |
| --- | --- |
| `agent.turn.start/end`, `agent.step.start/end` | Turn and model/tool-step boundaries |
| `agent.generation.start/end` | End includes `status` completed/incomplete/interrupted/failed, optional `usage`/`error` |
| `agent.output_item.started` | Provisional item/phase; no canonical write |
| `agent.text.delta/done` | `item_id`, `kind`, channel, `part_index`, Framework `sequence`, delta or complete text |
| `agent.tool_input.delta/done` | Incremental arguments/input for an identified tool item |
| `agent.remote_tool.status` | Service-side hosted tool: `tool`, `item_id`, started/running/completed/failed |
| `agent.tool_calls.start/end`, `agent.tool_call.start/end` | Local tool batch and individual call; feedback and eventual output |
| `agent.response_item.added` | Canonical item, emitted after its session write succeeds |
| `agent.provider_artifacts.updated` | Replace current provider presentations; no history commit |
| `agent.compaction.start/end` | Compaction lifecycle, response and retired archive anchor/items |
| `agent.interrupted`, `agent.error`, `agent.task.*` | Interruption/error and specialist task lifecycle |
| `agent.config.updated`, `agent.state.updated`, `agent.plugin.binding.updated` | Canonical settings and agent binding changes |

Text channels are `output`, `refusal`, `reasoning`, `summary`; they are not
interchangeable user-visible assistant text. A sequence is allocated per generation
by the Framework, not copied from backend `sequence_number`. Compare it within
that generation/item stream, track part identity, and reset transient state when
reconciling. Backend logprobs, opaque response envelopes, unknown diagnostics and
binary image/audio chunks do not become public event metadata or presentations.
For a generated image/audio presentation, the owning integration must explicitly
produce an [Artifact](artifacts.md).

## Hooks versus subscribers

The generation adapter converts known wire signals before running `on_event`.
For a converted `AgentResponseItemAdded`, the hook can replace its item **before**
commit; then the canonical writer appends it and emits the committed event to
subscribers. Seeing a text delta or output-item start does not imply that this
commit has happened. A write failure must not be displayed as a durable answer.

`on_event` is a generation transformation hook, not a callback for every event
from every tool/plugin. `after_generation` runs on the worker event loop after
stream processing, even on failure, and before local tool execution; its payload
is currently `None` and it may be sync or async. Keep feature decoding/storage in
the owning plugin. [Extensions](extensions.md) lists hook names and execution
contracts; [Tools](tools.md) describes tool acknowledgements versus output items.

## Hosted envelopes and recovery

The event socket sends `CommandEvent` (`type="command_event"`) with `request_id`,
`command_id`, nested `event` and optional canonical `watermark`. It also sends
acceptance/completion/failure/status envelopes. `SubagentEvent` adds private agent,
session and task identity; show it as transient child activity, not as primary
conversation text. The actual specialist result returns through ordered caller
input. Realtime owns its `realtime_event` envelope and `voice.*` event declarations.

The ordinary socket has no replay cursor; reconnect via history/status as in
[Conversation transports](conversation.md). Paired remote clients have a separate
sequenced transport; do not treat its sequence/replay protocol as a property of
every Framework event. Transient lifecycle/control signals (restart, UI refresh,
voice reconnect) are not conversation messages.

## Source and verification

`agent/models/{events,lifecycle}.py`, `agent/runtime/{event_loop,responses_events,
protocol,agentic_loop}.py` own the core contract. `plugins/realtime/{events,
live_events}.py` own voice conversion. `tests/general/test_framework_events.py`
checks normalized fields, unknown-type rejection, commit timing, plugin ownership,
usage and voice boundaries; `test_event_loop.py` checks producer lifetime. The
example above is executed by `test_documented_agent_contracts.py` without inference.
