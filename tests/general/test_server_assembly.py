from fastapi.testclient import TestClient

from the_framework import Application, AgentSpec, BuildContext
from the_framework.server.api.endpoints import Principal


def test_standard_assembly_keeps_authentication_and_includes_runtime_health(tmp_path):
    class Runtime:
        def health(self):
            return {"status": "ok", "worker": {"running": True}}

        async def request(self, request):
            return {"config": {"model": "example"}}

    class Security:
        async def authenticate(self, request):
            return Principal(id="local", scopes=frozenset({"*"})) if request.headers.get("authorization") == "Bearer test" else None

        async def authorize(self, principal, requirement, request):
            return principal.id == "local"

    application = Application(
        name="Example", version="1", security=Security(), surfaces=(),
        primary_agent=AgentSpec(name="primary", session_mode="durable"),
    )
    app = application.build(
        "example:application", BuildContext(data_root=tmp_path),
        supervisor=object(), session_factory=lambda supervisor, context: Runtime(),
        handshake=lambda socket: None,
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/config").status_code == 401
        client.headers["authorization"] = "Bearer test"
        health = client.get("/api/v1/health").json()
        assert health["status"] == "ok"
        assert health["worker"] == {"running": True}
        assert health["services"]["runtime"]["status"] == "running"
        assert client.get("/api/v1/config").json() == {"config": {"model": "example"}}
