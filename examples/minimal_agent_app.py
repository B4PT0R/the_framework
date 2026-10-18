"""Service-only Application; add primary_agent to host a conversation."""

from the_framework import Application, ServerExtension, Plugin, endpoint
from the_framework.server import ApplicationHealthApi
from the_framework.server.api.endpoints import Principal
from modict import modict


class Increment(modict):
    _config = modict.config(strict=True, extra="forbid")
    amount: int

    @modict.validator("amount", mode="after")
    def positive(self, value):
        if value < 1:
            raise ValueError("amount must be positive")
        return value


class CounterValue(modict):
    _config = modict.config(strict=True, extra="forbid")
    value: int


class BearerSecurity:
    async def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer example-secret":
            return None
        return Principal(id="example-client", scopes=frozenset({"counter:write", "health:read"}))

    async def authorize(self, principal, requirement, _request):
        if requirement is None:
            return True
        return requirement.get("scope") in principal.scopes


class CounterService:
    def __init__(self):
        self.value = 0
        self.running = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    def health(self):
        return {"value": self.value}

    @endpoint(
        "post", "/api/v1/counter/increment",
        request=Increment,
        response=CounterValue,
        authorization={"scope": "counter:write"},
    )
    def increment(self, amount):
        """Increment the application counter."""
        self.value += amount
        return {"value": self.value}


health_api = ApplicationHealthApi()

application = Application(
    name="Minimal Agent Application",
    version="1.0",
    security=BearerSecurity(),
    plugins=(Plugin(name="counter", server_extensions=ServerExtension(
        name="counter", service_factory=CounterService,
    )),),
    server_extensions=(
        ServerExtension(name="health", endpoints=(health_api.health,), requires_services=("counter",)),
    ),
)

app = application.build()
