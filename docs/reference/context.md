# Model input and compaction

`Context` belongs to one agent. It prepares Responses input from the canonical
`Session`, projects ephemeral providers and owns compaction. It does not own
client history or Realtime's separate projection. See [Sessions](sessions.md)
for storage and [Providers](providers.md) for live sampling.

## Inspect and compact without a model account

This program uses the actual agent/context/persistence path. Only the backend
compaction response is scripted; the opaque summary is test data, not a real
summary or valid content for a live backend.

```python
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from the_framework.agent import Agent, Context, InputText, Message, Session


class Compactor:
    def __init__(self):
        self.requests = []

    def compact(self, **payload):
        self.requests.append(payload)
        assert "live-temperature" not in json.dumps(payload["input"])
        return {
            "id": "anchor-1",
            "output": [{"type": "compaction_summary", "encrypted_content": "test-anchor"}],
            "usage": {"input_tokens": 30, "output_tokens": 7, "total_tokens": 37},
        }


with TemporaryDirectory() as directory:
    session = Session.open(Path(directory) / "session.json")
    backend = Compactor()
    agent = Agent(session=session, client=SimpleNamespace(responses=backend))
    agent.add_message("user", "Keep the final design red.")

    @agent.providers.add(channels=["text"])
    def temperature():
        return {"live-temperature": 18}

    before = list(session.history)
    # Pure projection: no provider sampling, repair or save.
    assert Context.project(before)[0]["role"] == "user"
    assert session.history == before
    prepared = asyncio.run(agent.context.ainput())
    assert "live-temperature" in json.dumps(prepared)
    assert "live-temperature" not in json.dumps(session)
    response = agent.compact()
    assert response.id == session.anchor_id == "anchor-1"
    assert session.anchor_token_count == 7
    assert len(backend.requests) == 1
    assert [item.type for item in session.history] == ["compaction_summary"]
    assert any(isinstance(item, Message) for item in session.archive)
    reopened = Session.open(session.path)
    assert reopened.anchor_id == "anchor-1"
    assert reopened.context_usage is None
```

## Exact preparation order

For an ordinary model step, `AgenticLoop.step()`:

1. Samples text-channel providers once and emits the ephemeral provider-artifact
   update. Async provider sampling is supported.
2. Builds input through `Context.ainput(provider_outputs)`: sanitizes canonical
   history, prepends the `context_prefix` hook, projects those items, then appends
   the sampled provider projection with `ephemeral=True`.
3. Builds the request from root configuration, rendered agentic instructions,
   tool schemas and input. `before_step` may transform this request; a per-turn
   reasoning override is applied afterwards.
4. Estimates local input size. If at threshold or explicitly requested, compacts
   the canonical context, rebuilds the request with the **same provider sample**,
   and refreshes the estimate. It does not loop until under threshold.
5. Generates, processes calls and commits results; another step samples providers
   afresh. `after_generation` runs in generation cleanup.

`Context.input()` is synchronous; `ainput()` supports async providers. Neither is
pure: canonical sanitization can save repairs. `Context.project(items,
max_input_images=0, ephemeral=False)` is the pure conversion primitive. It drops
older top-level `Image` items beyond the requested count, strips canonical
message metadata down to Responses fields, and converts artifacts using their
model representation. Projection does not copy provider outputs into history.
Canonical projection uses `max_input_images` (agent default 15); provider images
are projected separately and all images in that provider sample are retained.

## Recovery before projection

A worker sanitizes on startup and context preparation repeats the check. Missing
local `Image` and `copied_files` targets are pruned. An unresolved function call
receives a failure output at a user/compaction boundary or end of input. Duplicate
resolved outputs are removed from active history; archive duplicates are retained.
Unmatched outputs can be valid after an opaque summary and are not all discarded.

The repair says execution was interrupted with unknown side effects. It does not
rerun tools. Repairs update the revision and clear old usage; a pure display or
model projection does not perform this recovery on its own.

## Compaction request and commit

`agent.compact()` immediately invokes this agent's `Context.compact()`; call it
only at a safe execution boundary. The bundled agentic plugin's `compact` tool
instead requests compaction **before the next model step**, after the current
batch has recorded every tool result. Repeated requests coalesce. A graceful
turn end can defer that request until another step; the flag is in memory and
does not survive worker replacement. Failure leaves the request pending, not
acknowledged as completed.

Compaction sends canonical input plus `context_prefix` and current tool schemas
to `client.responses.compact()`. It does not sample providers, include their
latest outputs or render ordinary agentic instructions into that call.
Compaction has its own model/instructions/reasoning/text/cache configuration.
The Framework emits `agent.compaction.start` without the potentially sensitive
input; after an atomic commit it emits `agent.compaction.end` with the typed
response and retired anchor information. Failure is not a successful end event.

The returned output becomes active history. The archive adds this output and
keeps at most the configured number of compaction anchors; on overflow it drops
the prefix before the oldest retained anchor. Thus old conversation is available
only within the retained archive, **not forever**. Commit updates revision,
anchor id, anchor output-token count and tail boundary, and clears usage. Failed
snapshot writes restore the previous in-memory commit fields; a backend call and
a file write are not one cross-system transaction.

## Thresholds, estimates and cache keys

| Configuration | Current default | Meaning |
| --- | --- | --- |
| `compaction.context_token_limit` | `200_000` | Local budget; must be positive |
| `compaction.trigger_ratio` | `0.95` | Compact at `estimate >= limit * ratio`; must be in `(0, 1]` |
| `compaction.archive_anchor_limit` | `10` | Positive retained-anchor limit |
| `compaction.model` | `gpt-6-luna` | Declared source default, not a model availability guarantee |

Select a compaction model supported by your actual backend; it need not equal
the generation model. These defaults describe this checkout, not all recipes or
installed releases.

The local estimate counts request instructions/input/tools/tool choice/reasoning/
text. Image URLs and encrypted summaries use placeholders; a saved anchor's
reported output tokens are added when a summary is present. This is a local
planning estimate, not billing usage, an exact image-token calculation or proof
of the backend's accepted context size. `last_local_input_tokens` reflects the
last prepared request, not necessarily a new request currently being built.
Backend response usage is separately persisted as `session.context_usage` and
cache-hit statistics; context mutation/compaction can invalidate it.

Absent an explicit `prompt_cache_key`, generation and compaction each derive a
stable UUID5 from the session id. Explicit keys are preserved. Matching default
keys do not guarantee a backend cache hit or identical input prefixes.

## Observed cache-hit measurements

Worker status exposes the session's durable `cache_hit`: `ema_percent`,
`last_percent`, `samples`, `unreported`, `updated_at` and `alpha` (default 0.1).
A completed/incomplete generation with valid reported usage contributes
`100 * cached_tokens / input_tokens`. The first sample seeds the EMA; later ones
use `alpha * sample + (1 - alpha) * EMA`. It averages **per-call percentages**,
not tokens weighted by request size, billing savings or quota consumption.

```python
from the_framework.agent.models.usage import CacheHitStats, ResponseUsage

stats = CacheHitStats()
assert stats.ema_percent is None  # unknown is not zero
stats = stats.observe(ResponseUsage(
    input_tokens=100, output_tokens=10, total_tokens=110,
    input_tokens_details={"cached_tokens": 80},
))
assert stats.ema_percent == 80.0 and stats.samples == 1
missing = stats.observe(None)
assert missing.last_percent is None and missing.unreported == 1
assert missing.ema_percent == 80.0 and missing.updated_at == stats.updated_at
```

Missing/invalid usage clears the latest percentage and increments `unreported`
without changing the EMA, sample count or valid-sample Unix timestamp. Polling
status/reconnecting never samples again. The worker measures its own Responses
generations, not private specialists, voice or the distinct compaction endpoint.
Failed/interrupted streams without a completed/incomplete response provide no
observation. These metrics are not inserted into the model prompt.

**Source:** `agent/context/builder.py`, `agent/context/compaction.py`,
`agent/context/caching.py`, `agent/runtime/agentic_loop.py`,
`agent/models/config.py`, `plugins/agentic/agent.py`.
