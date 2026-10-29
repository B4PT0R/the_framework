# Tools, schemas and results

Function tools run Python in the agent worker. Hosted tools run in the configured
inference backend. Declaring a hosted or hybrid tool does not install a local
implementation or establish backend support.

Public imports below come from `the_framework.agent`; the `tool` decorator is
also exported by `the_framework`.

## Declare and inspect a function tool

```python
from typing import Literal
from the_framework import AgentExtension, AgentResources, AgentSpec, tool

class Catalogue(AgentExtension):
    name = "catalogue"

    @tool
    def find(self, query: str, tags: list[str] | None = None,
             order: Literal["recent", "title"] = "recent"):
        """
        description: Search the application catalogue and return matching titles.
        parameters:
          properties:
            query:
              description: Words to match in the title.
            tags:
              description: Restrict to these tags; null means no tag filter.
            order:
              description: Sort order for the matches.
        """
        if not query.strip():
            raise ValueError("query must not be blank")
        # Replace with your application's storage query.
        return {"query": query, "tags": tags, "order": order, "titles": []}

agent = AgentSpec(name="assistant", extensions=(Catalogue,)).build_agent(
    resources=AgentResources(client=object()),
)
namespace = agent.registry("tools")["catalogue"]
declaration = namespace.find("find")
assert declaration.strict is True
assert declaration.parameters.required == ["query", "tags", "order", "user_feedback"]
assert declaration.parameters.properties.tags["anyOf"][1] == {"type": "null"}
```

`client=object()` prevents accidental inference in this inspection example. It
cannot generate responses. The extension automatically groups decorated methods
under a `NamespaceTool` named `catalogue`.

The parser uses Python annotations for a best-effort JSON Schema and reads the
docstring as YAML. A plain YAML string becomes the description; a mapping can
set `name`, `description`, `parameters` and `strict`. Quote prose containing YAML
syntax, or use `description: |` for multiline text. Invalid YAML fails during
registration with the callable's location.

### Schema rules

| Python declaration | Inferred shape |
| --- | --- |
| `str`, `int`, `float`, `bool` | string, integer, number, boolean |
| `T | None` | nullable type or `anyOf` for a structured branch |
| `Literal["a", "b"]` | type plus enum |
| `list[T]`, `tuple[T, ...]`, `set[T]` | array, using the first type argument for items |
| `dict[str, T]` | object with values described by `T` |
| Other classes / unresolved annotations | Best-effort fallback; provide an explicit schema |

This parser is not a general serializer for arbitrary Python classes. A tuple
annotation does not produce a heterogeneous positional tuple schema. Signature
`*args` and `**kwargs` are skipped.

Documented properties merge into inferred properties. An explicitly documented
shape (`type`, `$ref`, `anyOf`, `oneOf`, `allOf` or `const`) replaces the inferred
shape while retaining compatible descriptive metadata. Use this to describe
nested objects precisely rather than relying on a custom annotation class.

Function tools default to `strict=True` and `additionalProperties=False`. Their
`required` list includes **every property**, including parameters with Python
defaults. A default does not make a tool argument optional in the model schema.
Use a nullable annotation when the model must be able to express “no value”.
A direct Python call can still omit a parameter with a default.

`strict: false` in YAML changes the emitted backend declaration. It does not
turn the worker into a JSON Schema validator. The executor parses a JSON object,
extracts feedback and invokes Python with keyword arguments. Validate application
constraints in your callable or shared request model, as the
[Notebook example](../guides/first-plugin.md) does.

### Required activity feedback

The Framework adds `user_feedback: string` to every `FunctionTool` schema and
requires a nonblank value on execution. Do not declare this parameter on the
Python function: it is reserved and registration rejects it. The executor removes
it before calling Python; activity events carry it for the client.

### Direct registration and lookup

For a one-off agent, `agent.add_tool(function)` or `@agent.add_tool` registers a
root function. `agent.tools.add(FunctionTool.from_function(function))` accepts an
explicit declaration. The decorator is needed for extension discovery, not for
an explicitly added callable.

`agent.registry("tools")` returns the effective registry, including active
extensions. `agent.tools` is the agent's own contribution registry. Adding a
namespace with a duplicate name or a duplicate nested tool raises an error.

`resolve(name, namespace)` selects one tool. Without a namespace, a root tool
wins; otherwise a single matching nested tool is accepted. Multiple matches are
ambiguous. Use the namespace explicitly across protocol boundaries.

## Execute without inference

This is a tool-pipeline test, not a generated conversation:

```python
import asyncio
import json
from xml.etree import ElementTree
from the_framework.agent import FunctionCall

async def exercise(agent):
    call = FunctionCall(
        name="find", namespace="catalogue", call_id="find-1",
        arguments=json.dumps({
            "query": "architecture", "tags": None, "order": "recent",
            "user_feedback": "I am searching the catalogue for architecture notes.",
        }),
    )
    receipt = await agent.agentic_loop.tool_output(call)
    ack = json.loads(receipt.output)
    assert ack["status"] == "success"
    assert ack["message_ids"] == [item.id for item in receipt.output_items]
    item = receipt.output_items[0]
    result = json.loads(ElementTree.fromstring(item.content[0].text).text)
    assert result["query"] == "architecture"

asyncio.run(exercise(agent))
```

`FunctionCallOutput.output` contains an acknowledgement (`status`, `message_ids`,
and `error` on failure), not the returned application data. The associated typed
items carry that data. A `ToolOutput` is bound into developer-message content
wrapped in `<tool_output ...>`; binding consumes its raw `output` field. Do not
inspect `bound_item.output` expecting the original Python object.

Calling `tool_output` directly does not append the receipt or items to session
history. Normal turns execute through `run_tool_call`, emit activity events,
apply `on_tool_output` and append outputs through the canonical agent owner.
Use `agent.astream` or a hosted worker to test the whole turn.

## Return values

| Return from the callable | Result |
| --- | --- |
| `None` | Successful acknowledgement, no contextual items |
| String / JSON-compatible object / list | One `ToolOutput` |
| `ToolOutput(output=value)` | Explicit text result, bound to the call |
| `Image(...)` | Vision input associated with the call |
| `Artifact(kind=..., payload=...)` | Presentation item associated with the call |
| Tuple of any of the above | Multiple items, skipping `None` entries |

A list is **one JSON value**, not a list of separate outputs. Return a tuple for
separate text, image and artifact items. Strings remain text; other ordinary
values are serialized as JSON with non-finite numbers rejected. An arbitrary
Python instance is not a portable result.

Text outputs are bounded by `AgentConfig.max_tool_output_tokens` using the
configured model's token estimator, with the middle truncated when necessary.
Store a large result as a file and return a concise preview plus an intentional
file artifact. Truncated text is not a complete record of the underlying data.
See [artifacts and uploads](artifacts.md) for persistence and client rendering.

## Scheduling, failures and interruption

- An `async def` tool is awaited on the worker's event loop. A synchronous tool
  runs through `asyncio.to_thread`; if it returns an awaitable, that is awaited.
- Use async tools for `agent.bridge.call`. Move blocking work off the event loop
  on both sides of a bridge, not only in the worker.
- Argument parsing, lookup, callable and serialization errors become failure
  acknowledgements plus error context. They are not proof that no side effect
  occurred before the error.
- A turn interruption does not roll back external effects or forcibly terminate
  a synchronous function already running in a thread. Remaining unstarted calls
  can receive interrupted receipts. Give long work explicit cancellable jobs
  and identifiers that make retries safe.
- `parallel_tool_calls` is passed to the backend; do not infer a transaction or
  ordering guarantee for application effects from that setting.

## Deferred discovery

`FunctionTool.from_function(function, defer_loading=True)` marks a function for
backend deferred loading. `ToolSearchTool(execution="client", ...)` can use the
Framework's local catalogue search. `Tools.search({"query": "note"})` matches
names/descriptions case-insensitively, retaining namespace wrappers; an omitted
query returns the catalogue except the search tool itself. Discovery does not
change binding, authorization or callable validation.

## Hosted and hybrid declarations

The package exports `WebSearchTool`, `FileSearchTool`, `ImageGenerationTool`,
`CodeInterpreterTool` and `McpTool` for backend-hosted capabilities. It also
exports `ShellTool`, `ComputerUseTool`, `ApplyPatchTool`, `LocalShellTool` and
`ToolSearchTool` as hybrid declarations. Inspect their typed fields in
[`extensions/tools.py`](../../the_framework/agent/extensions/tools.py).

These classes describe payloads understood by an inference adapter. Support,
credentials and execution ownership depend on the backend/application. A
`ShellTool` declaration is not the bundled Bash job service; an `McpTool` is not
an authorization policy for a remote server. Use the corresponding complete
plugin when you need Framework-managed execution or artifact storage.

Implementation: [`schema.py`](../../the_framework/agent/extensions/schema.py),
[`tools.py`](../../the_framework/agent/extensions/tools.py),
[`agentic_loop.py`](../../the_framework/agent/runtime/agentic_loop.py),
[`responses.py`](../../the_framework/agent/models/responses.py).
