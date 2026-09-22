import pytest

from the_framework.agent.extensions.endpoints import Endpoints
from the_framework.agent.extensions.plugin import AgentPlugin, endpoint


class ApiPlugin(AgentPlugin):
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


def test_plugin_exposes_endpoint_declarations():
    plugin = ApiPlugin(None)
    endpoint = plugin.endpoint_declarations()[0]

    assert endpoint() == {"ok": True}
    assert endpoint == {
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
    assert plugin.endpoints() == []


def test_endpoint_declaration_rejects_an_invalid_path():
    with pytest.raises(ValueError, match="must start with"):
        endpoint("get", "status")


def test_endpoint_registry_rejects_route_collisions():
    endpoint = ApiPlugin(None).endpoint_declarations()[0]
    endpoints = Endpoints()
    endpoints.add(endpoint)

    with pytest.raises(ValueError, match="duplicate endpoint: GET /status"):
        endpoints.add(endpoint)
