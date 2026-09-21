import inspect
from types import NoneType, UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from ..models.base import Base


class Param(Base):
    type: str | list[str]
    description: str


class Properties(Base[str, Param]):
    pass


class Parameters(Base):
    type: str = "object"
    properties: Properties
    required: list
    additionalProperties: bool = False

    @classmethod
    def from_dict(cls, data):
        return cls(
            type=data.get("type", "object"),
            properties=data.get("properties", {}),
            required=data.get("required", []),
            additionalProperties=data.get("additionalProperties", False),
        )


def parse_yaml_docstring(docstring, *, source="function docstring"):
    import yaml

    try:
        return yaml.safe_load(docstring)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ValueError(f"{source}: invalid YAML{location}") from error


def annotation_schema(annotation):
    if annotation is inspect.Signature.empty:
        return {}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is NoneType:
        return {"type": "null"}
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in (Union, UnionType):
        schemas = [annotation_schema(value) for value in arguments]
        if all(set(schema) == {"type"} for schema in schemas):
            return {"type": [schema["type"] for schema in schemas]}
        return {"anyOf": schemas}
    if origin is Literal:
        schema = annotation_schema(type(arguments[0])) if arguments else {}
        return {**schema, "enum": list(arguments)}
    if annotation in (list, tuple, set) or origin in (list, tuple, set):
        schema = {"type": "array"}
        if arguments:
            schema["items"] = annotation_schema(arguments[0])
        return schema
    if annotation is dict or origin is dict:
        schema = {"type": "object"}
        if len(arguments) == 2:
            schema["additionalProperties"] = annotation_schema(arguments[1])
        return schema
    return {}


def signature_parameters(func):
    signature = inspect.signature(func)
    try:
        annotations = get_type_hints(func)
    except (NameError, TypeError):
        # A callable can legitimately refer to an optional dependency or a
        # local-only symbol that is unavailable while its declaration is
        # inspected. Preserve the previous best-effort behavior in that case.
        annotations = {}
    properties = {}
    required = []
    for name, parameter in signature.parameters.items():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        properties[name] = annotation_schema(
            annotations.get(name, parameter.annotation)
        )
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def merge_parameters(signature_data, docstring_data):
    docstring_data = docstring_data or {}
    properties = signature_data.get("properties", {}).copy()
    for name, schema in docstring_data.get("properties", {}).items():
        properties[name] = {
            **properties.get(name, {}),
            **schema,
        }
    required = docstring_data.get("required", signature_data.get("required", []))
    return {
        "type": docstring_data.get("type", signature_data.get("type", "object")),
        "properties": properties,
        "required": required,
        "additionalProperties": docstring_data.get(
            "additionalProperties",
            signature_data.get("additionalProperties", False),
        ),
    }


def parse_function_specs(func):
    source = f"{func.__module__}.{func.__qualname__} docstring"
    metadata = parse_yaml_docstring(func.__doc__ or "", source=source)
    if metadata is None:
        metadata = {}
    if isinstance(metadata, str):
        metadata = {"description": metadata}
    if not isinstance(metadata, dict):
        raise ValueError(f"{source}: metadata must be a mapping or description text")
    parameters = metadata.get("parameters", {})
    if not isinstance(parameters, dict):
        raise ValueError(f"{source}: parameters must be a mapping")
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict) or any(
        not isinstance(value, dict) for value in properties.values()
    ):
        raise ValueError(f"{source}: parameters.properties must map names to schemas")
    return {
        **metadata,
        "name": metadata.get("name", func.__name__),
        "description": metadata.get("description", ""),
        "parameters": merge_parameters(
            signature_parameters(func),
            parameters,
        ),
    }
