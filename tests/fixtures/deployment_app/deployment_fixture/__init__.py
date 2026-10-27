"""Real Framework worker/HTTP lifecycle, with no plugins or inference calls."""

from hmac import compare_digest

from the_framework import Application, AgentSpec, BuildContext, ServerExtension
from the_framework.server import endpoint
from the_framework.server.api.endpoints import Principal

application = Application(
    name="Deployment fixture", version="1", surfaces=(),
    agents=(AgentSpec(name="primary"),),
)


class Security:
    def __init__(self, token):
        self.token = token

    async def authenticate(self, request):
        supplied = request.headers.get("authorization", "")
        if compare_digest(supplied.encode(), ("Bearer " + self.token).encode()):
            return Principal(id="local", scopes=frozenset({"*"}))

    async def authorize(self, principal, requirement, request):
        return principal.id == "local"


class RestartApi:
    def __init__(self, restart, validation):
        self.restart, self.validation = restart, validation

    @endpoint("post", "/fixture/restart", authorization={"scope": "agent:write"},
              response={"type": "object"})
    async def trigger(self):
        if self.validation:
            raise RuntimeError("no restarts during validation")
        await self.restart(None)
        return {"status": "restarting"}


def create_app(*, data_root, token, origin, browser_token, restart, validation):
    return application.build(
        "deployment_fixture:application",
        BuildContext(data_root=data_root, origin=origin, restart=restart, validation=validation),
        security=Security(token), handshake=lambda socket: None,
        session_extensions=lambda runtime: (
            ServerExtension(name="fixture_restart", scope="session",
                            endpoints=(RestartApi(restart, validation),)),
        ),
    )


def broken_app(**kwargs):
    raise RuntimeError("deliberate candidate startup failure")


def updated_app(**kwargs):
    return create_app(**kwargs)


def production_failure(**kwargs):
    if not kwargs['validation']:
        raise RuntimeError('deliberate production startup failure')
    return create_app(**kwargs)
