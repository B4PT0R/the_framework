import pytest

from fastapi import FastAPI
from the_framework.server.api.endpoints import EndpointRegistry, PermitAllSecurity, endpoint_declarations
from the_framework.server import endpoint


class ApiService:
    @endpoint("get", "/status", authorization={"scope": "status"})
    def status(self):
        """
        description: Return application status.
        response:
          type: object
          properties:
            ok:
              type: boolean
          required: [ok]
        """
        return {"ok": True}


def test_service_exposes_endpoint_declarations():
    service = ApiService()
    declaration = endpoint_declarations(service)[0]

    assert declaration() == {"ok": True}
    assert declaration == {
        "method": "GET",
        "path": "/status",
        "description": "Return application status.",
        "request": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "response": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
        "authenticated": True,
        "authorization": {"scope": "status"},
    }


def test_endpoint_declaration_rejects_an_invalid_path():
    with pytest.raises(ValueError, match="must start with"):
        endpoint("get", "status")


def test_endpoint_registry_rejects_route_collisions():
    registry = EndpointRegistry(FastAPI(), security=PermitAllSecurity())
    registry.add_source(ApiService())

    with pytest.raises(ValueError, match="duplicate.*GET /status"):
        registry.add_source(ApiService())


def test_http_discovery_does_not_load_or_activate_an_agent_extension():
    from fastapi.testclient import TestClient
    from the_framework import AgentExtension
    from the_framework.server.api.endpoints import register_endpoints

    class Feature(AgentExtension):
        name = "feature"

        def load(self):
            raise AssertionError("HTTP discovery must not load an agent extension")

        @endpoint("get", "/feature", authenticated=False)
        def status(self):
            return {"loaded": self.loaded, "activated": self.activated}

    feature = Feature(None)
    app = FastAPI()
    register_endpoints(app, [feature])
    with TestClient(app) as client:
        assert client.get("/feature").json() == {"loaded": False, "activated": False}
