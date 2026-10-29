# Public events

[Documentation](../README.md) / [Reference](README.md)

Use this when implementing a client or emitting plugin-owned events. Framework events, backend wire signals and canonical conversation items are different contracts.

## Public event contract

Agent streams, hooks, command sockets, specialist activity and realtime captions
use Framework events. Responses and Live wire events stay inside their adapters.
There is no raw-event passthrough: unknown backend diagnostics and binary chunks
are filtered. Known signals are converted by selecting declared fields. Backend
sequence numbers, logprobs and response envelopes are not public event metadata.

| Event | Meaning |
| --- | --- |
| `agent.turn.*`, `agent.step.*`, `agent.generation.start` | Worker execution lifecycle |
| `agent.generation.end` | `status` (completed/incomplete/interrupted/failed), optional usage and error |
| `agent.output_item.started` | Provisional item identity, role and phase; no history write |
| `agent.text.delta`, `agent.text.done` | Item text: `item_id`, `channel` (output/refusal/reasoning/summary), `part_index`, Framework `sequence`, and delta/text |
| `agent.tool_input.delta`, `agent.tool_input.done` | Streaming tool input, item identity and Framework sequence |
| `agent.remote_tool.status` | Inference-service tool execution: tool, item identity, started/running/completed/failed |
| `agent.response_item.added` | Canonical item, emitted once after the session write succeeds |
| `agent.tool_call.*`, `agent.tool_calls.*` | Execution of local tools and user-facing feedback |
| `agent.compaction.*`, `agent.interrupted`, `agent.task.*` | Context lifecycle, interruption and generic specialist requests/results |
| `agent.config.updated`, `agent.state.updated`, `agent.plugin.binding.updated` | Session-owned settings changes |
| `agent.provider_artifacts.updated` | Current-turn presentations; never canonical history |

The streaming sequence is allocated by the Framework per generation and compared
within an item's stream. Part indices identify text/summary parts. Output text
can be displayed provisionally; reasoning is a separate channel. Only canonical
item events and session pages supply durable chat history. Unknown or partial
image/audio chunks do not become chat presentations: use an explicit Artifact.
`on_event` sees converted Framework events before output items are committed;
returning a transformed `AgentResponseItemAdded` changes the item to commit.
Subscribers see that event only after persistence succeeds. Usage is recorded
internally and exposed on generation completion, without forwarding a response.

The optional `after_generation(payload)` hook runs on the worker event loop after
stream processing, including when the stream fails, and before local tool calls.
It receives `None` and may be synchronous or asynchronous. Plugins can use it to
await application capabilities (for example to publish their own durable outputs).
Keep feature-specific decoding, storage and presentation in the plugin, not the
Responses adapter; `on_event` can first transform an output into a local reference
before any binary payload reaches canonical history or downstream subscribers.

`Event.from_dict()` reconstructs declared types and rejects unknown types;
event fields reject undeclared keys. `CommandEvent.event`, `SubagentEvent.event`
and `RealtimeEvent.event` all enforce this boundary. The existing command,
worker and application-control envelopes remain Framework-owned protocols.
`AgentPluginBindingUpdated` names the typed binding event.

### Plugin-owned events

Declare an event where its behavior belongs. The core declares only events needed
for generic worker operation. Events used only by a plugin's feature lifecycle
are declared and emitted in that plugin, without a worker-core branch or central
feature-specific import. For example, in a plugin's `events.py`:

```python
from the_framework.agent import LocalEvent

class WeatherUpdated(LocalEvent):
    type: str = "weather.updated"
    temperature: float

# In the owning plugin:
# self.agent.emit(WeatherUpdated(temperature=18.5))
```

Import that plugin's event declarations in each Python process that reconstructs
them (normally when loading the plugin declaration). Type names follow the
existing dotted/underscored class naming convention. An extension to the event
vocabulary is explicit, not a raw backend catch-all.

The Realtime plugin owns `voice.transcript.updated` (role and cumulative text),
`voice.turn.completed` (role, turn id, transcript and interruption),
`voice.response.state`, `voice.speech.state` and `voice.error`. It also declares
its reconnect and delegation lifecycle events. Clients receive these typed
signals in `RealtimeEvent`; they do not assemble backend transcript fragments.
A completed voice turn clears the transient caption; the worker remains the
only writer of the canonical voice transcript. WebRTC carries client audio and
session negotiation remains an internal transport concern.

---

[Reference index](README.md) · [Documentation paths](../README.md)
