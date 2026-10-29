# Context providers

[Documentation](../README.md) / [Reference](README.md)

A provider gives the model current information when its input is prepared:
for example, a status snapshot or a fresh observation. Its output is temporary
context, refreshed separately from the saved conversation.

Channels select which projections receive the information, such as text or
realtime. See [Continuity](../explanation/continuity.md) for how working context
relates to durable history.

## Context providers

`@provider(channels=("text",))` contributes ephemeral context only to the named
projections. The declaration is honored both when registering the function with
`agent.providers.add(function)` and when loading it through an `AgentExtension`.
Channels are ordinary names; plugins may introduce their own projections.
Explicit `add(..., channels=...)` values override the decorator, including an
empty sequence. Empty channels exclude the provider from filtered projections;
unfiltered `outputs()` / `aoutputs()` still collect it. Without a channel override,
standalone providers keep the default `text` / `realtime` channels, while extension
providers use the extension's `provider_channels` (by default, `text`).

---

[Reference index](README.md) · [Documentation paths](../README.md)
