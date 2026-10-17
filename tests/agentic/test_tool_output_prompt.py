"""Editorial feedback for the current built-in tool-output instructions."""

from pathlib import Path
from the_framework.agent.extensions.instructions import Instruction

def test_builtin_tool_output_prompt_explains_message_references():
    instruction = Instruction.from_file(
        Path(__file__).parents[2] / "the_framework/agent/runtime/prompts/tool_outputs.md"
    )

    assert instruction.name == "tool_outputs"
    assert "message_ids" in instruction.content
    assert '<tool_output name="tool_name"' in instruction.content
    assert "call_id" in instruction.content
