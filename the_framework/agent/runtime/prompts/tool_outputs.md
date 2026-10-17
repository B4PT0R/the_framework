# Tool outputs

Direct function call outputs are execution acknowledgements, not the detailed
tool results. Their `status` reports whether the call succeeded or failed, and
their `message_ids` identify the result items, including developer messages and explicit artifacts.

Consult the referenced developer messages before continuing. Each result is
wrapped as:

```xml
<tool_output name="tool_name" id="message_id" call_id="originating_call_id">
...result...
</tool_output>
```

Use `id` to match a referenced message and `call_id` to associate it with the
function call that produced it.

Large results are truncated centrally to a configured token budget while
preserving both their beginning and their end. A marker reports the omitted
middle. When more detail is needed, narrow the request, use a search pattern or
pagination, and retrieve only the relevant section instead of requesting the
same broad output again.

Tools, commands and providers can return `Artifact(kind=..., payload=...)`
directly (or among tuple outputs). Tools and commands persist their artifacts; provider artifacts are temporary
presentations for the current turn and are never saved. An artifact is already
a presentation request:
do not publish it again just to show it. Its developer projection confirms its
presence in the canonical session, not successful rendering or user attention.
`Image` is exclusively a vision input and never requests chat display.
