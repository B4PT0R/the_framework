"""Responses wire adaptation. Only explicitly selected fields cross the boundary."""
from ..models.lifecycle import (
    AgentOutputItemStarted, AgentResponseItemAdded, AgentTextDelta, AgentTextDone,
    AgentToolInputDelta, AgentToolInputDone, AgentRemoteToolStatus,
)


class ResponsesEvents:
    """Per-generation sequence independent of backend sequence numbers."""
    def __init__(self):
        self.sequence = 0

    def convert(self, payload):
        if not isinstance(payload, dict):
            payload = payload.to_dict() if hasattr(payload, "to_dict") else payload.model_dump()
        kind = payload.get("type", "")
        if kind in {"response.output_item.added", "response.output_item.done"}:
            event = AgentOutputItemStarted if kind.endswith("added") else AgentResponseItemAdded
            return (event(item=payload["item"]),)
        text_channels = {
            "output_text": "output", "refusal": "refusal",
            "reasoning_text": "reasoning", "reasoning_summary_text": "summary",
        }
        parts = kind.split(".")
        if len(parts) == 3 and parts[0] == "response" and parts[2] in {"delta", "done"}:
            stream, action = parts[1:]
            if stream not in {*text_channels, "function_call_arguments", "custom_tool_call_input", "mcp_call_arguments", "code_interpreter_call_code"}:
                return ()
            common = {"item_id": payload["item_id"], "sequence": self.sequence}
            self.sequence += 1
            if stream in text_channels:
                index_key = "summary_index" if stream == "reasoning_summary_text" else "content_index"
                common.update(channel=text_channels[stream], part_index=payload.get(index_key, 0))
                event = (AgentTextDelta(delta=payload["delta"], **common) if action == "delta" else
                         AgentTextDone(text=payload.get("text", payload.get("refusal", "")), **common))
                return (event,)
            if stream in {"function_call_arguments", "custom_tool_call_input", "mcp_call_arguments", "code_interpreter_call_code"}:
                event = (AgentToolInputDelta(delta=payload["delta"], **common) if action == "delta" else
                         AgentToolInputDone(text=payload.get("arguments", payload.get("input", payload.get("code", ""))), **common))
                return (event,)
        remote_tools = {
            "web_search_call": "web_search", "file_search_call": "file_search",
            "code_interpreter_call": "code_interpreter", "image_generation_call": "image_generation",
            "mcp_call": "mcp", "mcp_list_tools": "mcp_discovery",
        }
        states = {"in_progress": "started", "searching": "running", "interpreting": "running",
                  "generating": "running", "completed": "completed", "failed": "failed"}
        if len(parts) == 3 and parts[0] == "response" and parts[1] in remote_tools and parts[2] in states:
            return (AgentRemoteToolStatus(item_id=payload["item_id"], tool=remote_tools[parts[1]], status=states[parts[2]]),)
        # Unknown diagnostics, content scaffolding, annotations and binary chunks
        # stay internal. A visible image/file must be an intentional Artifact.
        return ()
