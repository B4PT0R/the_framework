from the_framework.agent import AgentTrigger
from the_framework.agent.spec import AgentSpec
import pytest
from fastapi.testclient import TestClient

from the_framework.server import endpoint
from the_framework.server.composition.application import (
    Application,
    BuildContext,
    Capability,
    CapabilityRequirement,
    ServerExtension,
    Plugin,
    _assemble_server,
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
    options.setdefault("surfaces", ())
    return Application(
        name=name,
        version=version,
        agents=(AgentSpec(
            name="primary", description="Primary test agent."
        ),),
        **options,
    )


@pytest.mark.parametrize("application_owned", [False, True])
def test_compile_rejects_shared_service_names_before_construction(application_owned):
    constructed = []
    extension = ServerExtension(name="shared", service_factory=lambda: constructed.append(True))
    spec = application(
        "collision",
        server_extensions=(extension,) if application_owned else (),
        plugins=(
            *((Plugin(name="first", server_extensions=extension),) if not application_owned else ()),
            Plugin(name="second", server_extensions=extension),
        ),
    )
    with pytest.raises(ValueError, match="duplicate plugin runtime extension: shared"):
        _assemble_server(spec)
    assert constructed == []


def test_compile_allows_runtime_name_equal_to_another_plugin_identity():
    plan = application("distinct", plugins=(
        Plugin(name="first", server_extensions=ServerExtension(name="second")),
        Plugin(name="second", server_extensions=ServerExtension(name="third")),
    )).compile()
    assert plan.plugins["first"].server_extensions[0].name == "second"
    assert plan.plugins["second"].server_extensions[0].name == "third"


def test_plugin_runtime_factories_follow_capability_dependencies():
    built = []

    def provider(_context):
        built.append("provider")
        return ServerExtension(name="provider_service")

    def consumer(_context):
        assert built == ["provider"]
        built.append("consumer")
        return ServerExtension(name="consumer_service")

    spec = application("ordered runtimes", plugins=(
        Plugin(
            name="consumer", configure=consumer,
            requires_capabilities=(CapabilityRequirement(name="feature.provider"),),
        ),
        Plugin(
            name="provider", configure=provider,
            provides_capabilities=(Capability(name="feature.provider"),),
        ),
    ))

    _assemble_server(spec)
    assert built == ["provider", "consumer"]


def test_failed_capability_provider_prevents_dependent_service_start():
    events = []

    class Unavailable(Service):
        async def start(self):
            events.append("provider")
            raise RuntimeError("provider unavailable")

    app = _assemble_server(application("dependent services", plugins=(
        Plugin(
            name="consumer",
            requires_capabilities=(CapabilityRequirement(name="feature.provider"),),
            server_extensions=ServerExtension(
                name="consumer_service", service=Service(events), critical=False,
            ),
        ),
        Plugin(
            name="provider",
            provides_capabilities=(Capability(name="feature.provider"),),
            server_extensions=ServerExtension(
                name="provider_service", service=Unavailable(events), critical=False,
            ),
        ),
    )))

    with TestClient(app):
        graph = app.state.application.service_graph
        assert events == ["provider"]
        assert graph.started == []
        assert graph.errors == {
            "provider_service": "provider unavailable",
            "consumer_service": "application service dependencies are unavailable: provider_service",
        }
    assert events == ["provider"]


@pytest.mark.parametrize("factory", [False, True])
def test_single_service_plugin_has_explicit_construction_and_lifecycle(factory):
    events = []

    class Counter(Service):
        def __init__(self):
            super().__init__(events)
            self.value = 0

        @endpoint("post", "/counter", authenticated=False,
                  request={"type": "object", "properties": {},
                           "additionalProperties": False})
        def increment(self):
            """Increment the test counter."""
            self.value += 1
            return {"value": self.value}

    counter = Counter()
    component = ServerExtension(name="counter", **(
        {"service_factory": lambda: counter} if factory else {"service": counter}
    ))
    app = _assemble_server(application("Counter", plugins=(Plugin(name="counter", server_extensions=component),)))
    extension = app.state.application.extensions["counter"]
    assert extension.name == "counter"
    assert extension.service is counter
    with TestClient(app) as client:
        assert client.post("/counter").json() == {"value": 1}
        assert events == ["start"]
    assert events == ["start", "stop"]

    with pytest.raises(TypeError, match="server_extensions"):
        _assemble_server(application("Invalid", plugins=(Plugin(name="invalid", server_extensions=object()),)))


def test_compile_rejects_private_agent_shadowing_application_agent():
    spec = Application(
        name="collision", version="1",
        agents=(AgentSpec(name="plugin.agent"),),
        plugins=(Plugin(name="plugin", specialists=(AgentSpec(name="agent"),)),),
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
                agent_extension=object,
                server_extensions=ServerExtension(
                    name="echo_api",
                    endpoints=(echo,),
                    requires_services=("runtime",),
                ),
            ),
        ),
        server_extensions=(
            ServerExtension(name="runtime", service=Service(events)),
            ServerExtension(
                name="health", endpoints=(health.health,), requires_services=("runtime",),
            ),
        ),
    )
    app = _assemble_server(spec)

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
        _assemble_server(application(
            "Broken", "1", server_extensions=(ServerExtension(name="api", requires_services=("missing",)),)
        ))

    with pytest.raises(ValueError, match="security policy"):
        _assemble_server(application(
            "Unsafe", "1", server_extensions=(ServerExtension(name="api", endpoints=(echo,)),)
        ))

    with pytest.raises(ValueError, match="duplicate application endpoint"):
        _assemble_server(application(
            "Duplicate",
            "1",
            security=Security(),
            server_extensions=(
                ServerExtension(name="one", endpoints=(echo,)),
                ServerExtension(name="two", endpoints=(echo,)),
            ),
        ))

    with pytest.raises(ValueError, match="cyclic application extension"):
        _assemble_server(application(
            "Cyclic",
            "1",
            server_extensions=(
                ServerExtension(name="one", requires_services=("two",)),
                ServerExtension(name="two", requires_services=("one",)),
            ),
        ))


def test_application_declaration_is_immutable():
    base = application("Base")
    with pytest.raises(TypeError):
        base.server_extensions = (ServerExtension(name="runtime", service=object()),)
    assert base.server_extensions == ()


def test_server_build_preserves_the_definition_when_resolving_services():
    declaration = ServerExtension(name="service", service_factory=lambda: object())
    spec = application("Resolved services", server_extensions=(declaration,))
    plan = spec.compile()
    host = _assemble_server(spec).state.application

    assert host.plan == plan
    assert host.plan.application is spec
    assert spec.server_extensions[0] is declaration
    assert declaration.service is None
    assert host.extensions["service"].service is not None
    assert tuple(plugin.name for plugin in host.plugins) == tuple(host.plan.plugins)
    with pytest.raises(TypeError):
        host.extensions["extra"] = ServerExtension(name="extra")


def test_extension_factory_receives_its_named_service_dependencies():
    events = []
    dependency = Service(events)
    app = _assemble_server(application(
        "Factories",
        server_extensions=(
            ServerExtension(name="dependency", service=dependency),
            ServerExtension(
                name="dependent",
                service_factory=lambda dependency: {"dependency": dependency},
            ),
        ),
    ))

    with TestClient(app):
        assert app.state.application.service("dependent") == {
            "dependency": dependency,
        }

    with pytest.raises(ValueError, match="mutually exclusive"):
        ServerExtension(name="invalid", service=object(), service_factory=lambda: object())


def test_factory_injection_separates_inputs_from_additional_ordering_constraints():
    constructed = []
    dependency = object()

    def build(*, dependency, absent="fallback"):
        constructed.append((dependency, absent))
        return object()

    spec = application("Factory ordering", server_extensions=(
        ServerExtension(name="consumer", service_factory=build, requires_services=("barrier",)),
        ServerExtension(name="barrier", service_factory=lambda: constructed.append("barrier") or object()),
        ServerExtension(name="dependency", service=dependency),
    ))
    app = _assemble_server(spec)
    assert constructed == ["barrier", (dependency, "fallback")]
    assert tuple(app.state.application.extensions) == ("barrier", "dependency", "consumer")

    constructed.clear()
    with pytest.raises(ValueError, match="unknown.*dependency"):
        _assemble_server(application("Missing factory input", server_extensions=(
            ServerExtension(name="consumer", service_factory=build),
        )))
    assert constructed == []


def test_factory_injection_keeps_explicit_keyword_dependency_adapter():
    dependency = object()
    app = _assemble_server(application("Keyword factory", server_extensions=(
        ServerExtension(name="consumer", service_factory=lambda **services: services,
                        requires_services=("dependency",)),
        ServerExtension(name="dependency", service=dependency),
    )))
    assert app.state.application.extensions["consumer"].service == {"dependency": dependency}


@pytest.mark.parametrize("factory", [lambda dependency, /: object(), lambda *dependencies: object()])
def test_factory_injection_rejects_positional_only_inputs(factory):
    with pytest.raises(ValueError, match="requires keyword parameters"):
        ServerExtension(name="consumer", service_factory=factory)


def test_factory_injection_checks_optional_defaults_and_self_dependencies():
    with pytest.raises(ValueError, match="optional service parameter requires a default"):
        ServerExtension(name="consumer", service_factory=lambda dependency: object(),
                        optional_services=("dependency",))
    with pytest.raises(ValueError, match="cannot depend on itself"):
        ServerExtension(name="consumer", service_factory=lambda consumer: object())


def test_extension_factory_discovers_its_services_websockets():
    class SocketService:
        async def handle(self, socket):
            await socket.accept()
            await socket.send_json({"status": "ready"})
            await socket.close()

        def websocket_declarations(self):
            return (WebSocketEndpoint(
                path="/feature/events", handler=self.handle, authenticated=False,
            ),)

    app = _assemble_server(application(
        "Service sockets",
        server_extensions=(ServerExtension(
            name="feature", service_factory=SocketService,

        ),),
    ))

    with TestClient(app) as client:
        with client.websocket_connect("/feature/events") as socket:
            assert socket.receive_json() == {"status": "ready"}

    # No socket declaration means no socket, without a separate discovery flag.
    empty = _assemble_server(application(
        "No sockets", server_extensions=(ServerExtension(name="feature", service=object()),),
    ))
    assert empty.state.application.extensions["feature"].websockets == ()


def test_plugin_agent_binding_can_be_disabled_while_runtime_is_installed():
    app = _assemble_server(application(
        "Disabled Bundle",
        "1",
        plugins=(Plugin(
            name="echo",
            agent_extension=object,
            server_extensions=ServerExtension(name="echo_api", endpoints=(echo,)),
            agent_binding_enabled=False,
        ),),
        security=Security(),
    ))

    assert app.state.application.plugin_host.status("echo").binding_enabled is False
    assert app.state.application.plugin_host.status("echo").running is True


def test_compiler_keeps_server_factories_out_of_worker_projection():
    observed = []

    def runtime(context):
        observed.append(context.require("marker"))
        return ServerExtension(name="memory_runtime")

    spec = application(
        "Compiled",
        plugins=(
            Plugin(
                name="memory",
                configure=runtime,
                specialists=(AgentSpec(name="curator", description="Private memory curator."),),
                provides_capabilities=(Capability(name="memory.curate", version=2),),
            ),
            Plugin(
                name="consumer",
                requires_capabilities=(CapabilityRequirement(name="memory.curate", min_version=2),),
            ),
        ),
    )

    plan = spec.compile()

    assert observed == []
    assert tuple(plan.agents) == ("memory.curator", "primary")
    assert plan.capabilities["memory.curate"].version == 2
    app = _assemble_server(spec, context=BuildContext({"marker": "resolved"}))
    assert observed == ["resolved"]
    assert "memory_runtime" in app.state.application.extensions


def test_importable_session_factory_is_resolved_only_for_server_build(monkeypatch):
    from types import SimpleNamespace
    from the_framework.server.composition import application as composition

    imported = []

    def load(name):
        imported.append(name)
        return SimpleNamespace(factory=lambda context: ServerExtension(
            name=context.require("name")
        ))

    monkeypatch.setattr(composition, "import_module", load)
    spec = application("Lazy runtime", plugins=(Plugin(
        name="feature", configure="feature.server:factory",
    ),))
    spec.compile()
    assert imported == []
    app = _assemble_server(spec, context=BuildContext({"name": "feature_runtime"}))
    assert imported == ["feature.server"]
    assert "feature_runtime" in app.state.application.extensions

    with pytest.raises(ValueError, match="module:factory"):
        Plugin(name="invalid", configure="feature.server.factory")


def test_optional_capability_orders_present_provider_without_requiring_installation():
    observed = []
    prepared = object()

    def provider(context):
        return ServerExtension(name="provider_runtime", service=prepared)

    def consumer(context):
        def build(provider_runtime=None):
            observed.append((
                context.has_capability("example.provider"),
                context.has_capability("example.provider", min_version=2),
                provider_runtime,
            ))
            return object()
        return ServerExtension(name="consumer_runtime", service_factory=build)

    consumer_plugin = Plugin(
        name="consumer",
        configure=consumer,
        optional_capabilities=(CapabilityRequirement(name="example.provider"),),
    )
    provider_plugin = Plugin(
        name="provider",
        configure=provider,
        provides_capabilities=(Capability(name="example.provider"),),
    )
    with_provider = _assemble_server(application(
        "With optional provider", plugins=(consumer_plugin, provider_plugin),
    ), context=BuildContext())
    assert observed == [(True, False, prepared)]
    assert tuple(with_provider.state.application.extensions) == (
        "provider_runtime", "consumer_runtime",
    )

    without_provider = _assemble_server(application(
        "Without optional provider", plugins=(consumer_plugin,),
    ), context=BuildContext())
    assert observed == [(True, False, prepared), (False, False, None)]
    assert "provider_runtime" not in without_provider.state.application.extensions

    with pytest.raises(ValueError, match="cannot integrate capability example.provider>=2"):
        application("Incompatible optional provider", plugins=(
            Plugin(name="consumer", optional_capabilities=(
                CapabilityRequirement(name="example.provider", min_version=2),
            )),
            provider_plugin,
        )).compile()
    with pytest.raises(ValueError, match="duplicate plugin capability requirement"):
        Plugin(name="duplicate", requires_capabilities=(CapabilityRequirement(name="example.provider"),),
               optional_capabilities=(CapabilityRequirement(name="example.provider"),))


def test_optional_extension_service_is_ordered_and_injected_only_if_installed():
    observed = []
    provider = object()

    def build_consumer(provider_service=None):
        observed.append(provider_service)
        return object()

    consumer = ServerExtension(
        name="consumer", service_factory=build_consumer,
    )
    installed = _assemble_server(application("With service", server_extensions=(
        consumer, ServerExtension(name="provider_service", service=provider),
    )))
    assert tuple(installed.state.application.extensions) == (
        "provider_service", "consumer",
    )
    assert observed == [provider]

    absent = _assemble_server(application("Without service", server_extensions=(consumer,)))
    assert tuple(absent.state.application.extensions) == ("consumer",)
    assert observed == [provider, None]

    with pytest.raises(ValueError, match="cyclic application extension"):
        _assemble_server(application("Optional cycle", server_extensions=(
            consumer,
            ServerExtension(name="provider_service", requires_services=("consumer",)),
        )))
    with pytest.raises(ValueError, match="duplicate extension dependency"):
        ServerExtension(name="duplicate", requires_services=("provider_service",),
                  optional_services=("provider_service",))


def test_extension_discovers_decorated_methods_from_an_object():
    class Api:
        @endpoint("get", "/object-endpoint", authenticated=False)
        def read(self):
            """Read through an endpoint source object."""
            return {"ok": True}

    app = _assemble_server(application(
        "Object endpoints",
        server_extensions=(ServerExtension(name="api", endpoints=(Api(),)),),
    ))

    with TestClient(app) as client:
        assert client.get("/object-endpoint").json() == {"ok": True}


def test_application_agent_recipe_does_not_declare_session_lifetime():
    application = Application(
        name="In-memory application", version="1",
        agents=(AgentSpec(name="primary"),),
    )
    assert "retain_history" not in application.compile().agents["primary"]


@pytest.mark.parametrize("short_form", [False, True])
def test_plugin_private_agent_identities_are_namespaced_and_ephemeral_by_default(short_form):
    from the_framework.agent import AgentExtension, agent_trigger

    class Owner(AgentExtension):
        name = "owner" if short_form else "reusable_owner"

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
        "Private agents", plugins=(Owner if short_form else Plugin(name="owner", agent_extension=Owner),)
    ).compile()

    assert "owner.observer" in plan.agents
    assert not AgentTrigger(spec=plan.agents["owner.observer"]).profile().retain_history
    assert AgentTrigger(spec=plan.agents["owner.observer"]).queue.mode == "fifo"
    assert [profile.name for profile in plan.specialists] == [
        "owner.observer",
    ]
    agent = plan.agents["primary"].build_agent()
    assert "owner.observer" in agent.registry("agents")


def test_plugin_shorthand_normalizes_without_instantiating_the_plugin():
    from the_framework.agent import AgentExtension

    class Example(AgentExtension):
        name = "example"

        def __init__(self, _agent):
            raise AssertionError("compilation must not construct plugins")

    app = application("short", plugins=Example)
    assert app.plugins == (Plugin(name="example", agent_extension=Example),)
    assert app.compile().plugins["example"].agent_extension is Example
    with pytest.raises(ValueError, match="duplicate plugin"):
        application("duplicate", plugins=(Example, Plugin(name="example"))).compile()


def test_plugin_shorthand_requires_a_declared_identity():
    from the_framework.agent import AgentExtension

    class Anonymous(AgentExtension):
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

    app = _assemble_server(application(
        "Socket App",
        "1",
        server_extensions=(
            ServerExtension(
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
