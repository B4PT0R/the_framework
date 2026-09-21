"""Model endpoints share validation/defaults with their declared modict types."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from modict import modict
from typing import Literal
import pytest

from the_framework.agent import endpoint
from the_framework.agent.extensions.endpoints import Endpoint
from the_framework.server.api.endpoints import EndpointRegistry
from the_framework.server.api.model_schema import model_schema


class RequestModel(modict):
    _config = modict.config(strict=True, extra="forbid")
    amount: int
    factor: int = 2

    @modict.validator("amount", mode="after")
    def positive(self, value):
        if value < 1:
            raise ValueError("positive amount required")
        return value


class ReplyModel(modict):
    _config = modict.config(strict=True, extra="forbid")
    value: int


def client_for(handler):
    app = FastAPI()
    registry = EndpointRegistry(app)
    registry.add(Endpoint.from_function(handler))
    registry.install()
    return TestClient(app)


def test_model_defaults_validation_and_openapi():
    @endpoint("post", "/count", authenticated=False, request=RequestModel, response=ReplyModel)
    def count(amount, factor):
        return {"value": amount * factor}

    with client_for(count) as client:
        assert client.post("/count", json={"amount": 3}).json() == {"value": 6}
        for payload in ({}, {"amount": 0}, {"amount": "3"}, {"amount": 1, "extra": True}):
            assert client.post("/count", json=payload).status_code == 422
        schema = client.get("/openapi.json").json()["paths"]["/count"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert schema["required"] == ["amount"]
        assert schema["properties"]["factor"]["type"] == "integer"


def test_invalid_model_response_is_a_server_error():
    @endpoint("get", "/count", authenticated=False, response=ReplyModel)
    def count():
        return {"value": "wrong"}

    with client_for(count) as client:
        assert client.get("/count").status_code == 500


def test_schema_projection_does_not_run_factories_and_supports_nested_models():
    def unexpected():
        raise AssertionError("schema generation must not run defaults")

    class Nested(modict):
        rows: list[RequestModel] = modict.factory(unexpected)

    schema = model_schema(Nested)
    assert schema["required"] == []
    assert schema["properties"]["rows"]["items"]["required"] == ["amount"]


def test_unsupported_model_hint_requires_explicit_schema():
    class Unsupported(modict):
        value: complex

    with pytest.raises(TypeError, match="explicit JSON Schema"):
        model_schema(Unsupported)


def test_explicit_field_hint_takes_precedence_over_annotation():
    class Explicit(modict):
        value: int = modict.field(hint=str)

    assert model_schema(Explicit)["properties"]["value"] == {"type": "string"}


def test_query_models_preserve_transport_conversion_and_defaults():
    @endpoint("get", "/count", authenticated=False, request=RequestModel, response=ReplyModel)
    def count(amount, factor):
        return {"value": amount * factor}

    with client_for(count) as client:
        assert client.get("/count?amount=4").json() == {"value": 8}
        assert client.get("/count?amount=bad").status_code == 422


def test_nested_models_apply_native_validators():
    class Envelope(modict):
        item: RequestModel

    @endpoint("post", "/count", authenticated=False, request=Envelope, response=ReplyModel)
    def count(item):
        assert isinstance(item, RequestModel)
        return {"value": item.amount * item.factor}

    with client_for(count) as client:
        assert client.post("/count", json={"item": {"amount": 2}}).json() == {"value": 4}
        assert client.post("/count", json={"item": {"amount": -1}}).status_code == 422


def test_nullable_and_literal_query_fields_have_transport_types():
    class Query(modict):
        amount: int | None = None
        factor: Literal[1, 2] = 2

    @endpoint("get", "/count", authenticated=False, request=Query, response=ReplyModel)
    def count(amount, factor):
        return {"value": (amount or 0) * factor}

    with client_for(count) as client:
        assert client.get("/count?amount=3&factor=1").json() == {"value": 3}
        assert client.get("/count?factor=3").status_code == 422
