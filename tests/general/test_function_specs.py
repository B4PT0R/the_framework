from typing import Literal

import pytest
from jsonschema import Draft202012Validator

from the_framework.agent.extensions.tools import FunctionTool, tool


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


def test_function_tool_can_explicitly_allow_open_json_payloads():
    def example(payload: dict):
        """
        description: Accept application-defined JSON.
        strict: false
        """
        return payload

    declaration = FunctionTool.from_function(example)
    assert declaration.strict is False
    payload = {"widget": "weather", "forecast": [{"temperature": 18}]}
    arguments = {"payload": payload, "user_feedback": "Je présente la météo"}
    Draft202012Validator(declaration.parameters).validate(arguments)
    assert declaration(payload) == payload

    def ordinary(value: str):
        pass

    assert FunctionTool.from_function(ordinary).strict is True


def test_function_tool_rejects_non_boolean_strict_metadata():
    def example():
        """strict: 'false'"""

    with pytest.raises(ValueError, match="strict must be a boolean"):
        FunctionTool.from_function(example)


def test_tool_decorator_applies_user_feedback_and_reserves_its_argument_name():
    @tool
    def decorated():
        pass

    assert decorated.agent_user_feedback is True

    def conflicting(user_feedback: str):
        pass

    with pytest.raises(ValueError, match="reserved"):
        FunctionTool.from_function(conflicting)
