# Extension configuration, state, commands and hooks

An `AgentExtension` groups worker-side contributions under one binding. It can
supply tools, commands, instructions, providers, hooks and private specialists.
A `Plugin` pairs that binding with server declarations when the feature also
needs server resources. See the [Notebook tutorial](../guides/first-plugin.md)
for a complete example rather than isolated declarations.

## Load and binding lifecycle

Use `AgentSpec(extensions=(MyExtension,))`, or install a
`Plugin(name="feature", agent_extension=MyExtension)` in the application.
`AgentSpec` also accepts extension instances, factories receiving the agent, and
`module:attribute` references. Construct a fresh instance for each agent; an
already loaded extension is bound to its owner and cannot be freely renamed.
A Plugin's name determines the binding identity.

`load()` initializes typed configuration/state, reads instructions, discovers
decorated methods and records contributions. Tools are namespaced by the
extension's `title` (`name`, otherwise the class name). Commands, providers and
hooks use their own registry names: slash commands are not namespaced tools.
Decorated specialist identities are prefixed by the extension title.

`activate()` enables contributions and saves the binding. `deactivate()` removes
them from effective registries and saves the disabled binding, retaining the
instance and saved data. Registry collisions fail rather than silently picking
one implementation. `shutdown()` releases worker-local resources during worker
shutdown; it is not a server service lifecycle method.

The worker runs extension shutdown in reverse order. Server services have their
own service graph and are not stopped when an agent binding is disabled.

## Configuration and state

```python
from the_framework import AgentExtension, AgentResources, AgentSpec, Session
from the_framework.agent import Config, State

class Settings(Config):
    preview_limit: int = 5

class Progress(State):
    selected_project: str = "inbox"
    saves: int = 0

class Feature(AgentExtension):
    name = "feature"
    config = Settings
    state_schema = Progress

    def state_projection(self):
        return {"selected_project": self.plugin_state.selected_project}

spec = AgentSpec(name="assistant", extensions=(Feature,))
agent = spec.build_agent(Session(), resources=AgentResources(client=object()))
agent.update_config({"feature": {"preview_limit": 3}})
feature = agent.extension("feature")
feature.persist_state(selected_project="research", saves=1)
assert feature.config.preview_limit == 3
assert feature.plugin_state.saves == 1
assert agent.state_snapshot()["feature"] == {"selected_project": "research"}
```

Use `Session.open(path)` instead of `Session()` to persist across processes.
Configuration is user-selected behavior; state is mutable feature data. Both are
namespaced under the extension title in the canonical session. Neither belongs
in a transient provider snapshot or a service's ad hoc copy of agent settings.
Schemas subclass `Config` / `State` and use `modict` validation. Defaults apply
when creating the typed namespace; saved values override construction defaults.

`agent.update_config(updates)` validates a candidate, commits it through Session,
then updates live values and emits `AgentConfigUpdated`. Unknown root config
fields fail. `feature.persist_state(**updates)` delegates to
`agent.update_plugin_state(name, updates, emit=True)`: candidate validation and
the durable commit happen before the live object changes. A validation or save
failure leaves live and saved state unchanged. This is not a transaction covering
external database writes made before the call.

`state_projection()` defaults to the whole plugin state. Override it to expose
only client-safe fields; the durable state remains complete. `emit=False`
suppresses the state event, not persistence. A direct assignment to
`plugin_state`, `config`, `agent.config` or `agent.configs` is not the durable
update API and does not imply a canonical commit or client notification.

Set `restart_on_config_change=True` if applying new configuration requires worker
reconstruction. The hosted runtime asks `config_update_requires_restart` and
coordinates the update; a plain in-process `update_config` does not restart
anything. Use a server-owned secret/config source for credentials, not a
client-visible state or configuration projection.

## Instructions

An extension declares either `instructions` or `instructions_file`, not both:

```python
from the_framework import AgentExtension
from the_framework.agent import Instruction

class ReviewPolicy(AgentExtension):
    name = "review_policy"
    instructions = Instruction(
        name="source", scope="general",
        content="Distinguish verified findings from hypotheses.",
    )
```

The extension owns the registry name (`review_policy` here), wraps the content
with `# Plugin: review_policy`, and preserves the explicitly supplied scope.
A string uses `instruction_scope`, default `agentic`. A relative file path
resolves beside the module defining the extension class, not the current working
directory. File content is read during load; editing the file does not re-read an
already loaded extension.

`Instruction` supports `general`, `agentic` and `vocal` scopes. Rendering for a
scope includes `general` plus that scope; unfiltered rendering includes all.
`expires_at` is an optional Unix timestamp; expired entries are excluded when
rendered. `Instruction.from_file(path, name=None, scope="general", expires_at=None)`
constructs a standalone instruction. `agent.add_instruction(text, name="policy")`
is the convenient named registration path; a duplicate name fails.

## Slash commands

```python
from the_framework import command

@command
def select(project: str, count: int = 5, enabled: bool = True):
    """Select a project and presentation options."""
    return {"project": project, "count": count, "enabled": enabled}

agent.add_command(select)
outputs = agent.agentic_loop.command_outputs('/select "long project" count=2 enabled=no')
assert outputs[0].status == "success"
```

Normal `agent.astream('/select ...')` and hosted prompt commands run the command
and append its output without requesting inference. Direct `command_outputs`
only resolves and returns typed outputs; it does not itself save them.

Parsing starts only when the prompt is a string beginning with `/`. Arguments
are either a JSON object (when the trimmed argument string begins with `{`) or
shell-like tokens parsed by `shlex.split`. Tokens of the form `name=value` become
keywords; remaining tokens map to declaration-order parameters not already
supplied. Integer and number annotations convert tokens; boolean true values
are `1`, `true`, `yes`, `on` (case-insensitive), and all other strings convert to
false. Complex values should use JSON. This is not a full command-line parser;
validate required/allowed values in the handler rather than relying on token
parsing to reject every malformed invocation.

Commands are synchronous callables. The command path does not await an async
handler. Exceptions and output serialization failures become failure
`CommandOutput`s. An unknown command likewise fails without falling through to
the model. Return `None`, a JSON value, explicit `CommandOutput`, `Artifact`,
`Image`, or a tuple of separate values; a list stays one value. The
`on_command_output` hook receives the resulting list.

## Hooks: one handler per name

`@hook` exposes a method under its Python name. `agent.add_hook(function)` also
registers a standalone hook. A hook receives one payload and returns a
replacement, or `None` to keep the original payload. A missing hook leaves the
payload unchanged. Duplicate names are rejected across effective contributions;
there is no ordered list of handlers behind one hook name.

```python
from the_framework.agent import hook

@hook
def before_step(payload):
    return {**payload, "metadata": {"feature": "review"}}

agent.add_hook(before_step)
```

The call sites in this revision are:

| Name | Payload / timing | Execution boundary |
| --- | --- | --- |
| `before_turn` | Prompt before a turn or admitted steering input | Synchronous |
| `context_prefix` | Empty list; return model-input items to prepend | Synchronous |
| `before_step` | Prepared generation payload | Synchronous |
| `on_event` | Converted Framework event during response streaming | Generation thread, synchronous |
| `after_generation` | `None`, after stream success/failure, before tool execution | Async boundary, sync or async handler |
| `on_tool_output` | `FunctionCallOutput` after callable execution | Synchronous |
| `on_command_output` | List of typed command outputs | Synchronous |
| `on_external_<name>` | External command's payload | Ordered worker command, synchronous |
| `on_agent_result` | Private-agent result without a registered trigger | Ordered worker command, synchronous |
| `session_snapshot` | Snapshot request, watermark, items, archive, instructions, extensions | Synchronous |
| `on_shutdown` | `{"reason": "worker_shutdown"}` | Thread during shutdown, synchronous |

Transient external events also call `on_external_<name>` on the worker event
loop and await an awaitable return. That does **not** make the ordered
`ExternalEventRequest` path async: it invokes `Hooks.run` through the event
stream's synchronous producer. Prefer a synchronous handler for a name used by
both request types. Do not register an async function at a synchronous call site.

`Hooks.arun` awaits a returned awaitable; `Hooks.run` does not. `after_generation`
is the explicit boundary for awaited cleanup/bridge work after streaming.
Hooks are not an exception-isolation mechanism: a raised error propagates to
the owning operation, whose recovery depends on that operation. Keep response
stream hooks fast and do not mutate canonical history from an unrelated thread.

Source: [`extension.py`](../../the_framework/agent/extensions/extension.py),
[`commands.py`](../../the_framework/agent/extensions/commands.py),
[`hooks.py`](../../the_framework/agent/extensions/hooks.py),
[`agent.py`](../../the_framework/agent/runtime/agent.py),
[`worker.py`](../../the_framework/agent/runtime/worker.py).
