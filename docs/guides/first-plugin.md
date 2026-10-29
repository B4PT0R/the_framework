# Your first plugin

[Documentation](../README.md) / Build / First plugin

**Goal:** add one tool to the application created in
[Your first application](first-application.md). No HTTP route, service factory
or custom lifecycle is needed for this example.

## 1. Give the feature one job

Add the following imports and class to `starter/application.py`, keeping the
existing application and its other features:

```python
from datetime import datetime, timezone
from the_framework import AgentExtension, Plugin, tool


class Clock(AgentExtension):
    @tool
    def now(self):
        """Return the current time in UTC."""
        return datetime.now(timezone.utc).isoformat()


CLOCK = Plugin(name="clock", agent_extension=Clock)
```

`Clock` is the agent-side implementation. `CLOCK` is the complete feature
declaration. Tool instructions come from the method’s docstring, so make its
behavior clear. This tool has no state and needs no startup/shutdown hooks.

## 2. Install the declaration

Add `CLOCK` to the existing `Application(..., plugins=(...))` tuple:

```python
# Within your existing Application declaration:
plugins=(
    # Keep the existing declarations you want.
    CLOCK,
),
```

This is an **editing fragment**, not a second Application to paste beside the
first. Keep the original plugin entries in that tuple unless you intend to
remove their capabilities. Application-level plugins bind to its public agents
according to their binding policy.

## 3. Reconstruct and verify

After stopping the current local application, run it again with the same code
and data directories. Adding an installed plugin is a startup-composition change,
not a live tool toggle.

Ask the agent to use `clock.now` to report the current UTC time. Inspect the tool
result as well as the reply; an answer that merely guesses the time does not
verify the integration. This step uses inference and requires the backend setup
from the first guide.

## 4. Understand the two switches

**Installation** puts the feature in the application declaration. A plugin can
own agent tools, server services, routes and specialists as one coherent unit.
Removing it from startup composition requires a server restart.

**Agent binding** controls its exposure to a particular agent and can change
while the application runs. Disabling a binding does not remove installed routes,
stop shared services or delete saved plugin data. This distinction matters as
soon as a feature has settings or safety controls outside its tools.

Our clock has only an agent side. Do not add a server component just to imitate
a larger example.

## 5. Grow along the right boundary

| Your feature needs… | Add next |
| --- | --- |
| Current information in model context | A provider: [provider contract](../reference/providers.md) |
| Private work by another agent | A specialist: [agent contract](../reference/agents.md) |
| HTTP endpoints or a long-lived backend resource | `ServerExtension`: [services](../reference/services.md) |
| A contract shared with another plugin | Versioned capabilities: [composition](../reference/plugins.md) |
| Images, downloadable files or widgets in chat | [Artifact contract](../reference/artifacts.md) |

Import bundled features as complete declarations, for example
`from the_framework.plugins.scheduler import SCHEDULER`, rather than assembling
their worker/server halves yourself.

**Next:** [Development](development.md), or return to the
[documentation paths](../README.md).
