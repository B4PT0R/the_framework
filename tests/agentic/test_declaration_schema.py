import pytest
from typing import Literal

from jsonschema import Draft202012Validator

from harness_core.agent.extensions.endpoints import Endpoint, endpoint
from harness_core.agent.extensions.schema import parse_function_specs
from harness_core.agent.extensions.schema import annotation_schema
from harness_core.agent.extensions.tools import FunctionTool


@pytest.mark.parametrize("build", [FunctionTool.from_function, Endpoint.from_function])
def test_invalid_yaml_identifies_declaration_without_printing(build, capsys):
    @endpoint("POST", "/example")
    def broken(value: str):
        """description: [unfinished"""

    with pytest.raises(ValueError, match=r"broken docstring: invalid YAML at line") as caught:
        build(broken)
    assert caught.value.__cause__ is not None
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("document", ["false", "[]", "42", "parameters: []", "parameters: null", "parameters: {properties: []}", "parameters: {properties: {value: string}}"])
def test_invalid_metadata_is_not_silently_ignored(document):
    def malformed(value: str):
        pass

    malformed.__doc__ = document
    with pytest.raises(ValueError, match="malformed docstring:"):
        parse_function_specs(malformed)


@pytest.mark.parametrize("document, description", [(None, ""), ("", ""), ("A plain description.", "A plain description."), ("description: Structured description.", "Structured description.")])
def test_supported_descriptions_preserve_signature(document, description):
    def valid(value: str, count: int = 1):
        pass

    valid.__doc__ = document
    specs = parse_function_specs(valid)
    assert specs["description"] == description
    assert specs["parameters"]["required"] == ["value"]
    assert specs["parameters"]["properties"]["count"] == {"type": "integer"}


def test_nullable_literal_accepts_null_without_weakening_enum():
    validator = Draft202012Validator(annotation_schema(Literal["left", "right"] | None))
    for value in (None, "left", "right"):
        assert validator.is_valid(value)
    for value in ("other", 0, False):
        assert not validator.is_valid(value)
