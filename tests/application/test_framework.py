from the_framework.agent.spec import AgentSpec, SessionPolicy
import pytest
from fastapi.testclient import TestClient

from the_framework.agent.extensions.plugin import endpoint
from the_framework.server.composition.application import (
    AgentApplication,
    BuildContext,
    Capability,
    CapabilityRequirement,
    Extension,
    Plugin,
    build_application,
)
from the_framework.server.api.health import ApplicationHealthApi
from the_framework.server.api.endpoints import Principal
from the_framework.server.api.websockets import WebSocketEndpoint


class Security:
    async def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer secret":
            return None
        return Principal(id="test", scopes=frozenset({"read", "health:read"}))

    async def authorize(self, principal, requirement, _request):
        return requirement is None or requirement["scope"] in principal.scopes


class Service:
    def __init__(self, events):
        self.events = events

    async def start(self):
        self.events.append("start")

    async def stop(self):
        self.events.append("stop")


def application(name, version="1", **options):
    return AgentApplication(
        name=name,
        version=version,
        primary_agent=AgentSpec(
            name="primary", description="Primary test agent.", session=SessionPolicy.durable()
        ),
        **options,
    )


@pytest.mark.parametrize("application_owned", [False, True])
def test_compile_rejects_shared_service_names_before_construction(application_owned):
    constructed = []
    extension = Extension(name="shared", service_factory=lambda: constructed.append(True))
    spec = application(
        "collision",
        extensions=(extension,) if application_owned else (),
        plugins=(
            *((Plugin(name="first", runtime=extension),) if not application_owned else ()),
            Plugin(name="second", runtime=extension),
        ),
    )
    with pytest.raises(ValueError, match="duplicate plugin runtime extension: shared"):
        spec.build()
    assert constructed == []


def test_compile_allows_runtime_name_equal_to_another_plugin_identity():
    plan = application("distinct", plugins=(
        Plugin(name="first", runtime=Extension(name="second")),
        Plugin(name="second", runtime=Extension(name="third")),
    )).compile()
    assert plan.plugin_extensions["first"][0].name == "second"
    assert plan.plugin_extensions["second"][0].name == "third"


def test_compile_rejects_private_agent_shadowing_primary():
    spec = AgentApplication(
        name="collision", version="1",
        primary_agent=AgentSpec(name="plugin.agent", session=SessionPolicy.durable()),
        plugins=(Plugin(name="plugin", agents=(AgentSpec(name="agent"),)),),
    )
    with pytest.raises(ValueError, match="duplicate private agent identity"):
        spec.compile()


@endpoint(
    "post",
    "/echo/{item_id}",
    request={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "count": {"type": "integer", "minimum": 1},
        },
        "required": ["item_id", "count"],
        "additionalProperties": False,
    },
    response={
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["item_id", "count"],
        "additionalProperties": False,
    },
    authorization={"scope": "read"},
)
def echo(item_id: str, count: int):
    """Echo one validated payload."""
    return {"item_id": item_id, "count": count}


def test_application_spec_builds_a_small_complete_server():
    events = []
    health = ApplicationHealthApi()
    spec = application(
        name="Example Agent App",
        version="1.0",
        security=Security(),
        plugins=(
            Plugin(
                name="echo",
                agent=object,
                runtime=Extension(
                    name="echo_api",
                    endpoints=(echo,),
                    requires=("runtime",),
                ),
            ),
        ),
        extensions=(
            Extension(name="runtime", service=Service(events)),
            Extension(
                name="health", endpoints=(health.health,), requires=("runtime",),
            ),
        ),
    )
    app = build_application(spec)

    with TestClient(app) as client:
        assert events == ["start"]
        unauthorized = client.post("/echo/one", json={"count": 2})
        response = client.post(
            "/echo/one",
            json={"count": 2},
            headers={"Authorization": "Bearer secret"},
        )
        invalid = client.post(
            "/echo/one",
            json={"count": 0},
            headers={"Authorization": "Bearer secret"},
        )
        health_response = client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer secret"},
        )
        assert app.state.application.service("runtime").events is events

    assert events == ["start", "stop"]
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "unauthorized"
    assert response.json() == {"item_id": "one", "count": 2}
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert health_response.json() == {
        "status": "ok",
        "services": {
            "runtime": {"status": "running", "critical": True},
        },
    }


def test_composition_errors_are_reported_before_the_server_starts():
    with pytest.raises(ValueError, match="unknown.*missing"):
        build_application(application(
            "Broken", "1", extensions=(Extension(name="api", requires=("missing",)),)
        ))

    with pytest.raises(ValueError, match="security policy"):
        build_application(application(
            "Unsafe", "1", extensions=(Extension(name="api", endpoints=(echo,)),)
        ))

    with pytest.raises(ValueError, match="duplicate application endpoint"):
        build_application(application(
            "Duplicate",
            "1",
            security=Security(),
            extensions=(
                Extension(name="one", endpoints=(echo,)),
                Extension(name="two", endpoints=(echo,)),
            ),
        ))

    with pytest.raises(ValueError, match="cyclic application extension"):
        build_application(application(
            "Cyclic",
            "1",
            extensions=(
                Extension(name="one", requires=("two",)),
                Extension(name="two", requires=("one",)),
            ),
        ))


def test_specs_are_immutable_and_extend_without_side_effects():
    base = application("Base")
    extended = base.with_extensions(Extension(name="runtime", service=object()))

    assert base.extensions == ()
    assert [extension.name for extension in extended.extensions] == ["runtime"]


def test_extension_factory_receives_declared_service_dependencies():
    events = []
    dependency = Service(events)
    app = build_application(application(
        "Factories",
        extensions=(
            Extension(name="dependency", service=dependency),
            Extension(
                name="dependent",
                service_factory=lambda dependency: {"dependency": dependency},
                requires=("dependency",),
            ),
        ),
    ))

    with TestClient(app):
        assert app.state.application.service("dependent") == {
            "dependency": dependency,
        }

    with pytest.raises(ValueError, match="mutually exclusive"):
        Extension(name="invalid", service=object(), service_factory=lambda: object())


def test_plugin_agent_binding_can_be_disabled_while_runtime_is_installed():
    app = build_application(application(
        "Disabled Bundle",
        "1",
        plugins=(Plugin(
            name="echo",
            agent=object,
            runtime=Extension(name="echo_api", endpoints=(echo,)),
            binding_enabled=False,
        ),),
        security=Security(),
    ))

    assert app.state.application.plugins["echo"].binding_enabled is False
    assert app.state.application.plugin_host.status("echo").running is True


def test_compiler_keeps_server_factories_out_of_worker_projection():
    observed = []

    def runtime(context):
        observed.append(context.require("marker"))
        return Extension(name="memory_runtime")

    spec = application(
        "Compiled",
        plugins=(
            Plugin(
                name="memory",
                runtime=runtime,
                agents=(AgentSpec(name="curator", description="Private memory curator."),),
                capabilities=(Capability(name="memory.curate", version=2),),
            ),
            Plugin(
                name="consumer",
                requires=(CapabilityRequirement(name="memory.curate", min_version=2),),
            ),
        ),
    )

    plan = spec.compile()

    assert observed == []
    assert tuple(plan.agents) == ("memory.curator", "primary")
    assert plan.capabilities["memory.curate"].version == 2
    app = spec.build(BuildContext({"marker": "resolved"}))
    assert observed == ["resolved"]
    assert app.state.application.plan.plugin_extensions["memory"][0].name == "memory_runtime"


def test_importable_runtime_factory_is_resolved_only_for_server_build(monkeypatch):
    from types import SimpleNamespace
    from the_framework.server.composition import application as composition

    imported = []

    def load(name):
        imported.append(name)
        return SimpleNamespace(factory=lambda context: Extension(
            name=context.require("name")
        ))

    monkeypatch.setattr(composition, "import_module", load)
    spec = application("Lazy runtime", plugins=(Plugin(
        name="feature", runtime="feature.server:factory",
    ),))
    assert spec.compile().plugin_extensions == {}
    assert imported == []
    app = spec.build(BuildContext({"name": "feature_runtime"}))
    assert imported == ["feature.server"]
    assert app.state.application.plan.plugin_extensions["feature"][0].name == "feature_runtime"

    with pytest.raises(ValueError, match="module:factory"):
        Plugin(name="invalid", runtime="feature.server.factory")


def test_extension_discovers_decorated_methods_from_an_object():
    class Api:
        @endpoint("get", "/object-endpoint", authenticated=False)
        def read(self):
            """Read through an endpoint source object."""
            return {"ok": True}

    app = build_application(application(
        "Object endpoints",
        extensions=(Extension(name="api", endpoints=(Api(),)),),
    ))

    with TestClient(app) as client:
        assert client.get("/object-endpoint").json() == {"ok": True}


def test_primary_agent_must_be_durable():
    from the_framework.agent import SessionPolicy

    with pytest.raises(ValueError, match="primary agent session must be durable"):
        AgentApplication(
            name="Invalid",
            version="1",
            primary_agent=AgentSpec(
                name="primary",
                description="Primary test agent.",
                session=SessionPolicy.ephemeral(),
            ),
        )


@pytest.mark.parametrize("short_form", [False, True])
def test_plugin_private_agent_identities_are_namespaced_and_ephemeral_by_default(short_form):
    from the_framework.agent import AgentPlugin, agent_trigger

    class Owner(AgentPlugin):
        name = "owner"

        @agent_trigger(
            AgentSpec(
                name="observer",
                instructions="Observe privately.",
                configuration={"model": "gpt-5.6-luna"},
            )
        )
        def observer(self, result):
            return result

    plan = application(
        "Private agents", plugins=(Owner if short_form else Plugin(name="owner", agent=Owner),)
    ).compile()

    assert "owner.observer" in plan.agents
    assert plan.agents["owner.observer"].session.mode == "ephemeral"
    assert plan.agents["owner.observer"].queue.mode == "fifo"
    assert [profile.name for profile in plan.primary_agent.specialists] == [
        "owner.observer",
    ]


def test_plugin_shorthand_normalizes_without_instantiating_the_plugin():
    from the_framework.agent import AgentPlugin

    class Example(AgentPlugin):
        name = "example"

        def __init__(self, _agent):
            raise AssertionError("compilation must not construct plugins")

    app = application("short", plugins=Example)
    assert app.plugins == (Plugin(name="example", agent=Example),)
    assert app.compile().plugins["example"].agent is Example
    with pytest.raises(ValueError, match="duplicate plugin"):
        application("duplicate", plugins=(Example, Plugin(name="example"))).compile()


def test_plugin_shorthand_requires_a_declared_identity():
    from the_framework.agent import AgentPlugin

    class Anonymous(AgentPlugin):
        pass

    with pytest.raises(ValueError, match="explicit name"):
        application("anonymous", plugins=(Anonymous,))
    with pytest.raises(TypeError):
        application("invalid", plugins=(object,))


def test_custom_websocket_handshake_injects_protocol_context():
    def handshake(socket):
        if socket.headers.get("x-private-token") != "secret":
            return None
        return {"subprotocol": "example-v1", "client_id": "client-1"}

    async def serve(socket, connection):
        await socket.accept(subprotocol=connection["subprotocol"])
        await socket.send_json({"client_id": connection["client_id"]})

    app = build_application(application(
        "Socket App",
        "1",
        extensions=(
            Extension(
                name="socket",
                websockets=(WebSocketEndpoint(
                    path="/socket", handler=serve, handshake=handshake,
                ),),
            ),
        ),
    ))

    with TestClient(app) as client:
        with client.websocket_connect(
            "/socket",
            headers={"x-private-token": "secret"},
            subprotocols=["example-v1"],
        ) as socket:
            assert socket.receive_json() == {"client_id": "client-1"}
