"""Small standalone application built only from the_framework."""

from the_framework import AgentApplication, AgentSpec, Extension, Plugin, SessionPolicy, endpoint
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


counter = CounterService()
health_api = ApplicationHealthApi()

application = AgentApplication(
    name="Minimal Agent Application",
    version="1.0",
    primary_agent=AgentSpec(
        name="example",
        description="Minimal example agent.",
        session=SessionPolicy.durable(),
    ),
    security=BearerSecurity(),
    plugins=(Plugin(name="counter", runtime=counter),),
    extensions=(
        Extension(name="health", endpoints=(health_api.health,), requires=("counter",)),
    ),
)

app = application.build()
