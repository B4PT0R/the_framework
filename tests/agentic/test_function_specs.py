from typing import Literal

import pytest
from jsonschema import Draft202012Validator

from harness_core.agent.extensions.tools import FunctionTool, tool


def test_function_tool_schema_supports_optional_and_generic_annotations():
    def example(
        duration: float | None = None,
        channels: list[str] | None = None,
        kind: Literal["scalar", "linear", "rotate"] = "scalar",
    ):
        pass

    parameters = FunctionTool.from_function(example).parameters

    assert parameters.properties.duration.type == ["number", "null"]
    channels = Draft202012Validator(parameters.properties.channels)
    assert channels.is_valid(None)
    assert channels.is_valid(["vibrate"])
    assert not channels.is_valid([42])
    assert not channels.is_valid("vibrate")
    assert parameters.properties.kind["enum"] == ["scalar", "linear", "rotate"]
    assert parameters.properties.user_feedback.type == "string"
    assert "roughly ten words" in parameters.properties.user_feedback.description
    assert parameters.required == ["duration", "channels", "kind", "user_feedback"]


def test_function_tool_schema_resolves_postponed_annotations():
    def example(query: "str", limit: "int | None" = None):
        pass

    parameters = FunctionTool.from_function(example).parameters

    assert parameters.properties.query.type == "string"
    assert parameters.properties.limit.type == ["integer", "null"]


def test_tool_decorator_applies_user_feedback_and_reserves_its_argument_name():
    @tool
    def decorated():
        pass

    assert decorated.agent_user_feedback is True

    def conflicting(user_feedback: str):
        pass

    with pytest.raises(ValueError, match="reserved"):
        FunctionTool.from_function(conflicting)
