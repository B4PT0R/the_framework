"""Requirement inheritance must agree between runtime models and JSON Schema."""

import pytest
from modict import MISSING, modict
from modict.model_api import Field

from the_framework.agent.models.base import Base
from the_framework.agent.runtime.protocol import PromptRequest
from the_framework.utils.model_schema import model_schema


@pytest.mark.parametrize("default", ("never", "at_init", "always"))
@pytest.mark.parametrize("required", (MISSING, "never", "at_init", "always", True, False))
def test_schema_matches_effective_requirement(default, required):
    class Model(Base):
        _config = Base.config(default_required=default)
        value: int = modict.field(required=required)
        optional: int = modict.field(required=False)
        fallback: int = 1

    effective = default if required is MISSING else required
    mandatory = effective not in ("never", False)
    permanent = effective in ("always", True)
    assert model_schema(Model)["required"] == (["value"] if mandatory else [])
    if mandatory:
        with pytest.raises(KeyError):
            Model()
    else:
        Model().validate()
    model = Model(value=2)
    if permanent:
        with pytest.raises(TypeError):
            del model["value"]
        assert model.value == 2
    else:
        del model["value"]
        if mandatory:
            with pytest.raises(KeyError):
                model.validate()
        else:
            model.validate()


def test_explicit_subclass_override_and_missing_field_merge():
    class Parent(Base):
        value: int = modict.field(required="always")

    class Optional(Parent):
        value: int = modict.field(required="never")

    assert Optional.__fields__["value"].required == "never"
    assert model_schema(Optional)["required"] == []
    Optional().validate()
    assert Field().merge(Field(required="always")).required == "always"
    assert Field(required="never").merge(Field(required="always")).required == "never"
    assert Field().merge(Field()).required is MISSING


def test_wire_required_exceptions_survive_permissive_base():
    assert Base._config.default_required == "never"
    Base().validate()
    for values in ({}, {"id": "request"}, {"prompt": "hello"}):
        with pytest.raises(KeyError):
            PromptRequest(**values)
    request = PromptRequest(id="request", prompt="hello")
    request.validate()
    for name in ("id", "prompt"):
        with pytest.raises(TypeError):
            request.pop(name)
