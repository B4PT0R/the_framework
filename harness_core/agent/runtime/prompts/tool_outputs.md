# Tool outputs

Direct function call outputs are execution acknowledgements, not the detailed
tool results. Their `status` reports whether the call succeeded or failed, and
their `message_ids` identify the developer messages containing the results.

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
