# Plugins and capabilities

A `Plugin` is an installed feature: an agent extension, server components,
private specialist invocations, and versioned contracts. `AgentExtension` is
executable worker behavior; `ServerExtension` describes a server component.
Neither is a second installation system.

Import a bundled complete declaration, such as `BASH` or `SCHEDULER`, into
`Application.plugins`. See [bundled primitives](primitives.md) for their options
and dependencies. The [Notebook tutorial](../guides/first-plugin.md) implements
both halves of a custom feature, including storage, HTTP and actual worker calls.

## Declaration contract

| `Plugin` field | Contract |
| --- | --- |
| `name` | Nonempty letters/numbers/underscores; identity for config, state, tools, bindings and specialist prefix. |
| `agent_extension` | Worker class/factory/object/import reference, or `None` for server-only. |
| `server_extensions` | Explicit `ServerExtension` declarations, normalized to a tuple. |
| `configure` | Sync callable or `module:factory`; receives BuildContext and returns declarations or None. Runs at server build, not worker compile. |
| `specialists` | Private `AgentTrigger` declarations; bare `AgentSpec` gets default invocation policy. Names are qualified with the plugin name. |
| `provides_capabilities` | `Capability(name="namespace.contract", version=1)` contracts. |
| `requires_capabilities` | Required providers at `min_version` or later. |
| `optional_capabilities` | May be absent; if present, must satisfy the version and participates in ordering. |
| `agent_binding_enabled` | Initial worker binding, defaults true. |
| `agent_binding_required` | Prevent disabling; requires an agent extension and an initially enabled binding. |

An extension class passed directly to `Application.plugins` must declare `name`;
it becomes an agent-only Plugin. A class used directly in `AgentSpec.extensions`
keeps its own identity instead. An already loaded extension cannot be renamed.
Plugin private agents and public agents share a validated identity catalogue.

Declarations are frozen. Customize without copying implementation details:
construct `Plugin({**original, "agent_binding_required": True})`, for example.
This does not mutate `original` or any running installation.

## Capabilities are contracts, services are objects

A capability is a versioned *declaration*, not an automatically callable RPC or
an object lookup. Its owner defines its meaning. A service dependency names the
actual server object to inject. A worker bridge capability is a registered
handler string: register it separately (see [hosting](hosting.md#application-bridge)).

Cross-plugin service dependencies require a matching versioned capability edge.
Compilation rejects missing/too-old required providers, incompatible present
optional providers, duplicate exports and dependency cycles. Server assembly
rejects undeclared cross-plugin service edges. An optional installed provider is
ordered before its consumer; optional does not mean “ignore provider failure.”

This complete service-only composition uses both contracts. Plugin configuration
collects policy first; factories see every contribution. No server listener or
model is needed:

```python
from fastapi.testclient import TestClient
from the_framework import (
    Application, BuildContext, Capability, CapabilityRequirement,
    Plugin, ServerExtension,
)

class Labels:
    def __init__(self, context):
        self.allowed = frozenset(context.contributions("labels.allowed"))

class Exporter:
    def __init__(self, labels):
        self.labels = labels

LABELS = Plugin(
    name="labels",
    provides_capabilities=Capability(name="labels.catalog", version=2),
    server_extensions=ServerExtension(name="labels", service_factory=Labels),
)

def configure_exports(context):
    context.contribute("labels.allowed", "published")
    assert context.has_capability("labels.catalog", min_version=2)
    return ServerExtension(name="exporter", service_factory=Exporter)

EXPORTS = Plugin(
    name="exports",
    requires_capabilities=CapabilityRequirement(name="labels.catalog", min_version=2),
    configure=configure_exports,
)
application = Application(
    name="Label exports", version="1", surfaces=(), plugins=(EXPORTS, LABELS),
)
context = BuildContext(plugins={"exports": {"format": "jsonl"}})
app = application.build(context=context)
assert context.plugin("exports")["format"] == "jsonl"
with TestClient(app):
    labels = application.service("labels")
    assert labels.allowed == {"published"}
    assert application.service("exporter").labels is labels
    assert application.service_graph.started == ["labels", "exporter"]
try:
    context.contribute("labels.allowed", "late")
except RuntimeError:
    pass
else:
    raise AssertionError("contributions must be sealed before service construction")
```

All configure hooks run before service factories. The consumer's contribution
therefore reaches Labels even though the provider is ordered first. Configure
must collect declarations/policy, not start services. Feature owners define
contribution names and values; the framework does not interpret them.

## BuildContext phases

`BuildContext` belongs to one build. Reusing it for another is rejected.

- Mapping keys are fixed construction inputs. `data_root` is expanded/resolved;
  `session_path` and `surface_root` default below it. `require(key)` gives a
  diagnostic KeyError if missing. A value named like a service is not DI.
- `plugins={name: options}` is removed from the input mapping and kept as
  read-only **shallow** option snapshots. `context.plugin(name)` returns an
  empty read-only mapping for unspecified plugins. Nested objects are not
  deep-frozen or serialized to the worker.
- `context.application` gives the owner during factory construction, before
  `Application.current()` is available.
- `context.contribute(name, value)` collects process-local declarations;
  `contributions(name)` returns a tuple. Contributions are sealed before factories.
- `context.services` is a read-only live view of successfully started services,
  for deferred operational callbacks. It is not a constructor injection registry.

Application/session assembly has separate contexts. Configure hooks run with
session-specific inputs too; do not cache their outputs globally or assume a
hook runs only once per server. Agent configuration/state belongs to `Session`,
not these process-local inputs.

Use `configure="my_app.feature.server:configure"` and string service factories
to keep server implementation imports out of worker compilation. A callable
already imported in the declaration cannot undo its import-time effects.

## Installed runtime versus agent binding

The declared server graph is fixed until shutdown. Every installed plugin's
routes/services remain available even when its agent binding is disabled.
Disabling changes tools, providers and activation in one worker; its saved
Session owns that binding state. Required bindings cannot be disabled. Installed
server-only features have no worker binding to toggle.

`PluginHost.status()` reports declaration/runtime/binding information;
`set_binding(name, enabled, ...)` coordinates with the worker, and only updates
its cache after worker confirmation. A live binding is not proof that a client
capability is connected. A missing route is not repaired by enabling a binding.
For controls and correlated suspension points see [hosting](hosting.md).

Removing or replacing installed server code requires a new composition and
server replacement. Do not mutate FastAPI routes or global registries to emulate
runtime uninstallation. [Service lifecycle](services.md#lifecycle-and-failure)
covers optional failure, probes and cleanup.

Sources: `server/composition/declarations.py`, `application.py`, `plugins.py`;
`agent/extensions/extension.py`, `agent/spec.py`.
