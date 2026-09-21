"""Small OpenAPI projection of modict fields; modict owns runtime validation."""

from types import NoneType, UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from modict import MISSING, modict


def model_schema(model, seen=()):
    """Project supported JSON fields without constructing models or defaults.

    Python validators are deliberately not reverse-engineered into JSON Schema.
    Unsupported/recursive hints require an explicit endpoint schema instead.
    """
    if model in seen:
        raise TypeError("recursive endpoint model requires an explicit JSON Schema")
    hints = get_type_hints(model)
    properties = {}
    required = []
    for name, field in model.__fields__.items():
        hint = field.hint
        if isinstance(hint, str):
            hint = hints.get(name, hint)
        properties[name] = _hint_schema(hint, (*seen, model))
        if field.default is MISSING and (
            field.required != "never" or model._config.require_all != "never"
        ):
            required.append(name)
    return {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": model._config.extra != "forbid",
    }


def _hint_schema(hint, seen):
    scalars = {str: "string", bool: "boolean", int: "integer", float: "number", NoneType: "null"}
    if hint in scalars:
        return {"type": scalars[hint]}
    if hint in (Any, None):
        return {}
    if isinstance(hint, type) and issubclass(hint, modict):
        return model_schema(hint, seen)
    origin, args = get_origin(hint), get_args(hint)
    if origin in (Union, UnionType):
        choices = [_hint_schema(value, seen) for value in args]
        if all(set(choice) == {"type"} for choice in choices):
            return {"type": [choice["type"] for choice in choices]}
        return {"anyOf": choices}
    if origin is Literal:
        kinds = {type(value) for value in args}
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        return {**({"type": scalars[kind]} if kind in scalars else {}), "enum": list(args)}
    if hint is list or origin is list:
        return {"type": "array", "items": _hint_schema(args[0], seen) if args else {}}
    if hint is dict or origin is dict:
        if args and args[0] is not str:
            raise TypeError("endpoint model mappings require string keys")
        return {"type": "object", "additionalProperties": _hint_schema(args[1], seen) if args else {}}
    raise TypeError(f"unsupported endpoint model hint {hint!r}; provide explicit JSON Schema")
