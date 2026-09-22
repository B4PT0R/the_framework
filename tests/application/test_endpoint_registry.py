from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from the_framework.agent.extensions.plugin import AgentPlugin, endpoint
from the_framework.server.api.endpoints import PermitAllSecurity, register_plugin_endpoints


class EndpointPlugin(AgentPlugin):
    @endpoint(
        "post",
        "/upload",
        authenticated=False,
        request_encoding="multipart",
        request={
            "type": "object",
            "properties": {"files": {"type": "array", "minItems": 1}},
            "required": ["files"],
            "additionalProperties": False,
        },
        response={"type": "object"},
    )
    async def upload(self, files):
        """Receive repeated multipart fields as one list."""
        return {"names": [file.filename for file in files]}

    @endpoint(
        "post", "/accepted", authenticated=False, status_code=202,
        response={"type": "object"},
    )
    def accepted(self):
        """Return one accepted command receipt."""
        return {"status": "submitted"}

    @endpoint("delete", "/empty", authenticated=False, status_code=204)
    def empty(self):
        """Return no response body."""

    @endpoint(
        "get",
        "/context",
        request={"type": "object", "properties": {}, "additionalProperties": False},
        response={"type": "object"},
        context="call",
    )
    def context(self, call):
        """Read opt-in request context."""
        return {
            "path": call.request.url.path,
            "principal": call.principal.id,
        }

    @endpoint("post", "/inferred", authenticated=False)
    def inferred(self, label: str):
        """
        description: Exercise a schema inferred from the function signature.
        parameters:
          properties:
            label:
              type: string
          required: [label]
        response:
          type: object
        """
        return {"label": label}

    @endpoint(
        "get",
        "/query",
        request={
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
                "enabled": {"type": "boolean"},
            },
            "required": ["count", "enabled"],
            "additionalProperties": False,
        },
        response={"type": "object"},
    )
    def query(self, count: int, enabled: bool):
        """Read typed query values."""
        return {"count": count, "enabled": enabled}

    @endpoint("get", "/response", response={"type": "object"})
    def response(self):
        """Return a native response without JSON coercion."""
        return PlainTextResponse("hello")

    @endpoint(
        "get",
        "/items/{item_id}",
        authenticated=False,
        request={
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["item_id"],
            "additionalProperties": False,
        },
        response={
            "type": "object",
            "properties": {
                "item_id": {"type": "integer"},
                "tags": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["item_id", "tags"],
        },
    )
    def typed_path(self, item_id, tags=None):
        """Read recursively coerced path and repeated-query values."""
        return {"item_id": item_id, "tags": tags or []}

    @endpoint("get", "/bug", authenticated=False)
    def bug(self):
        """Exercise the opaque internal-error boundary."""
        raise ValueError("private implementation detail")


def test_endpoint_registry_coerces_queries_and_preserves_responses():
    app = FastAPI()
    register_plugin_endpoints(
        app, [EndpointPlugin(None)], security=PermitAllSecurity()
    )

    with TestClient(app) as client:
        query = client.get("/query?count=3&enabled=true")
        response = client.get("/response")

    assert query.json() == {"count": 3, "enabled": True}
    assert response.text == "hello"
    assert response.headers["content-type"].startswith("text/plain")


def test_endpoint_registry_returns_stable_validation_errors():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        missing = client.get("/query?count=3")
        malformed = client.get("/query?count=nope&enabled=true")

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_request"
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "invalid_request"


def test_endpoint_registry_accepts_inferred_modict_schemas():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.post("/inferred", json={"label": "ready"})

    assert response.json() == {"label": "ready"}


def test_endpoint_context_is_injected_without_entering_the_request_schema():
    app = FastAPI()
    plugin = EndpointPlugin(None)
    register_plugin_endpoints(app, [plugin])
    declaration = next(
        value for value in plugin.endpoint_declarations()
        if value.path == "/context"
    )

    with TestClient(app) as client:
        response = client.get("/context")

    assert "call" not in declaration.request["properties"]
    assert response.json() == {"path": "/context", "principal": "local"}


def test_endpoint_status_code_is_declarative():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.post("/accepted")

    assert response.status_code == 202

    with TestClient(app) as client:
        empty = client.delete("/empty")
    assert empty.status_code == 204
    assert empty.content == b""


def test_endpoint_registry_groups_repeated_multipart_files():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.post("/upload", files=[
            ("files", ("one.txt", b"one", "text/plain")),
            ("files", ("two.txt", b"two", "text/plain")),
        ])

    assert response.json() == {"names": ["one.txt", "two.txt"]}


def test_endpoint_registry_keeps_single_multipart_array_as_a_list():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.post(
            "/upload", files=[("files", ("one.txt", b"one", "text/plain"))]
        )

    assert response.json() == {"names": ["one.txt"]}


def test_endpoint_registry_coerces_path_and_array_items_recursively():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.get("/items/7?tags=2&tags=3")

    assert response.json() == {"item_id": 7, "tags": [2, 3]}


def test_endpoint_schemas_are_exposed_in_openapi():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    operation = app.openapi()["paths"]["/items/{item_id}"]["get"]
    assert operation["parameters"] == [
        {
            "name": "item_id",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
        },
        {
            "name": "tags",
            "in": "query",
            "required": False,
            "schema": {"type": "array", "items": {"type": "integer"}},
        },
    ]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["properties"]["item_id"] == {"type": "integer"}


def test_uncontrolled_handler_errors_are_opaque():
    app = FastAPI()
    register_plugin_endpoints(app, [EndpointPlugin(None)])

    with TestClient(app) as client:
        response = client.get("/bug")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private implementation detail" not in response.text
    assert response.json()["error"]["details"]["error_id"]
