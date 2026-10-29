# Context providers

Providers contribute observations to model input: a selected project, a current
status, browser content or an image. They are sampled anew, not appended to the
canonical conversation. Use a tool for an intentional operation and a provider
for a bounded snapshot that remains useful at the next model step.

## Declare, sample and inspect

This example needs no inference client or server:

```python
import asyncio
from the_framework import AgentExtension, AgentResources, AgentSpec, provider

class Workspace(AgentExtension):
    name = "workspace"

    @provider(channels=("text",))
    async def workspace_status(self):
        """Currently selected workspace and open changes."""
        return {"root": "/work/project", "changed_files": ["README.md"]}

agent = AgentSpec(name="assistant", extensions=(Workspace,)).build_agent(
    resources=AgentResources(client=object()),
)

async def sample():
    registry = agent.registry("providers")
    items = await registry.aoutputs(channel="text")
    assert items[0].name == "workspace_status"
    assert '"README.md"' in items[0].content[0].text
    assert await registry.aoutputs(channel="realtime") == []
    assert not agent.session.history

asyncio.run(sample())
```

Replace the example's static return with a bounded read from your feature.
The [Notebook provider](../../examples/notebook/agent.py) awaits the application
bridge and supplies recent titles rather than every saved note body.

A provider takes no invocation arguments. Registration parses its name and
description through the same YAML docstring machinery as tools. The synchronous
and async registry methods preserve registration order; providers are sampled
sequentially, not concurrently.

## Sampling points and channels

Text generation samples effective providers with `channel="text"` before each
step. If that step needs compaction, its already sampled outputs are reused when
rebuilding the generation payload. Compaction itself never samples or persists
providers. Realtime uses its own projection and channel; a provider does not
implicitly subscribe the voice engine to arbitrary live server events.

| Registration | Default channels |
| --- | --- |
| Standalone `agent.add_provider(function)` / `Providers.add(function)` | `text`, `realtime` |
| Decorated method on an `AgentExtension` | Extension's `provider_channels`, default `text` |
| `@provider(channels=(...))` | Explicit decorator value |
| `.add(function, channels=(...))` | Explicit registration value overrides decorator |

An empty channel sequence excludes a provider from filtered samples. Unfiltered
`outputs()` / `aoutputs()` still collect it. Channels are names; applications may
add projections using their own names.

A synchronous provider is called directly, even from `aoutputs()`. It therefore
blocks the sampling event loop if it does blocking work. An async provider is
awaited. `outputs()` can run an async provider using `asyncio.run` only when no
loop is active; otherwise it raises and asks for `aoutputs()`.

## Return values and lifetime

| Return | Produced items |
| --- | --- |
| `None` | None |
| String / JSON object / list | One bound `ProviderOutput` |
| `ProviderOutput(output=...)` | Explicit provider message |
| `Image(...)` | Ephemeral vision input |
| `Artifact(kind=..., payload=...)` | Ephemeral presentation request |
| Tuple | Separate items, skipping `None` |

Strings remain text; other ordinary values use JSON serialization. A list is
one value, not multiple output items. Binding a `ProviderOutput` consumes its raw
`output` and places rendered text in `content`, under
`<context_provider name="..." ...>`. Read that bound content rather than relying
on the original Python object remaining attached. Provider content is evidence,
not a higher-priority source of instructions.

Every provider result is ephemeral, including artifacts. The text input replaces
observations on the next sample; provider artifacts are presented for the current
turn and never enter the saved session. An old screenshot or observation is not
recoverable merely because it was once sampled. If later work depends on a
conclusion, commit the relevant conclusion through a deliberate state/session
operation before removing its observation source.

## Failures, budgets and cleanup

Provider exceptions and serialization failures propagate to the caller. They do
not automatically become a tool-style failure acknowledgement. If unavailable
context is expected, catch the appropriate error in the provider and return a
small explicit status, as Notebook does. Do not silently report an empty dataset
when the data source failed.

There is no automatic provider-specific text truncation equivalent to
`max_tool_output_tokens`. Bound rows, fields and text sizes at the source.
Do not serialize credentials, arbitrary disk trees or the complete conversation
into every sample. For asynchronous resources, use a declared service lifecycle
and an async provider instead of opening an unowned background task per sample.

Standalone registration: `agent.add_provider(function, description=None,
channels=None)`; the decorator form `@agent.add_provider(channels=("text",))`
also works. Duplicate provider names fail. Disabling an extension's binding
removes its provider from the effective registry without stopping its server
service.

Source: [`providers.py`](../../the_framework/agent/extensions/providers.py),
[`context/builder.py`](../../the_framework/agent/context/builder.py),
[`agentic_loop.py`](../../the_framework/agent/runtime/agentic_loop.py).
