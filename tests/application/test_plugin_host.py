import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from the_framework import (
    AgentApplication,
    AgentSpec,
    BuildContext,
    Capability,
    CapabilityRequirement,
    Extension,
    Plugin,
    SessionPolicy,
    endpoint,
)
from the_framework.server.api.endpoints import Principal


class Service:
    def __init__(self, events, *, fail=False):
        self.events = events
        self.fail = fail

    async def start(self):
        self.events.append("start")
        if self.fail:
            raise RuntimeError("failed")

    async def stop(self):
        self.events.append("stop")


class Api:
    @endpoint("get", "/plugin/value", authenticated=False)
    def value(self):
        """Return a plugin value."""
        return {"value": 1}


class Security:
    async def authenticate(self, request):
        if request.headers.get("authorization") == "Bearer secret":
            return Principal(id="test", scopes=frozenset({"plugins:read", "plugins:write"}))

    async def authorize(self, principal, requirement, _request):
        return requirement["scope"] in principal.scopes


def declaration(service, **plugin_options):
    return AgentApplication(
        name="Plugin host",
        version="1",
        primary_agent=AgentSpec(
            name="primary", description="Primary test agent.", session=SessionPolicy.durable()
        ),
        plugins=(Plugin(
            name="example",
            agent=object,
            runtime=Extension(name="example_runtime", service=service, endpoints=(Api(),)),
            **plugin_options,
        ),),
    )


def test_runtime_and_binding_transitions_are_independent_and_persistent(tmp_path):
    async def scenario():
        events = []
        bindings = []
        private_agents = []

        async def update_binding(name, enabled):
            bindings.append((name, enabled))

        async def update_runtime(name, running):
            private_agents.append((name, running))

        context = BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
            "plugin_binding_update": update_binding,
            "plugin_runtime_update": update_runtime,
        })
        app = declaration(Service(events)).build(context)
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/plugin/value")).status_code == 200
                await host.set_binding("example", False)
                assert (await client.get("/plugin/value")).status_code == 200
                await host.stop_runtime("example")
                assert (await client.get("/plugin/value")).status_code == 404
                status = await host.start_runtime("example")
                assert status.binding_enabled is False
                assert (await client.get("/plugin/value")).status_code == 200
                await host.set_binding("example", True)
        assert bindings == [
            ("example", False),
            ("example", True),
        ]
        assert private_agents == [
            ("example", True),
            ("example", False),
            ("example", True),
        ]
        assert events == ["start", "stop", "start", "stop"]

        restored = declaration(Service([])).build(context)
        status = restored.state.application.plugin_host.status("example")
        assert status.running is True
        assert status.binding_enabled is True

    asyncio.run(scenario())


def test_plugin_owns_multiple_ordered_server_components_and_hot_routes():
    async def scenario():
        events = []

        class NamedService:
            def __init__(self, name):
                self.name = name

            async def start(self):
                events.append(f"start:{self.name}")

            async def stop(self):
                events.append(f"stop:{self.name}")

        def runtime(_context):
            return (
                Extension(
                    name="feature_api",
                    service_factory=lambda feature_store: NamedService("api"),
                    requires=("feature_store",),
                    endpoints=(Api(),),
                ),
                Extension(name="feature_store", service=NamedService("store")),
            )

        spec = AgentApplication(
            name="Vertical feature",
            version="1",
            primary_agent=AgentSpec(
                name="primary", session=SessionPolicy.durable()
            ),
            plugins=(Plugin(name="feature", runtime=runtime),),
        )
        assert spec.compile().plugin_extensions == {}
        app = spec.build()
        assert tuple(
            item.name for item in app.state.application.plan.plugin_extensions["feature"]
        ) == ("feature_store", "feature_api")
        assert app.state.application.plugin_host.status("feature").binding_enabled is False
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
            with pytest.raises(RuntimeError, match="no agent binding"):
                await host.set_binding("feature", True)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/plugin/value")).status_code == 200
                await host.stop_runtime("feature")
                assert (await client.get("/plugin/value")).status_code == 404
                await host.start_runtime("feature")
                assert (await client.get("/plugin/value")).status_code == 200
        assert events == [
            "start:store", "start:api", "stop:api", "stop:store",
            "start:store", "start:api", "stop:api", "stop:store",
        ]

    asyncio.run(scenario())


def test_grouped_plugin_start_failure_rolls_back_earlier_services():
    async def scenario():
        events = []
        base = declaration(Service(events))
        plugin = Plugin(
            name="example",
            runtime=(
                Extension(name="first", service=Service(events)),
                Extension(
                    name="second",
                    service=Service(events, fail=True),
                    requires=("first",),
                ),
            ),
        )
        spec = AgentApplication({**base, "plugins": (plugin,)})
        failing = spec.build()
        with pytest.raises(RuntimeError, match="failed"):
            async with failing.router.lifespan_context(failing):
                pass
        assert events == ["start", "start", "stop"]
        assert failing.state.application.services.snapshot() == {}

    asyncio.run(scenario())


def test_full_stop_does_not_reenable_binding_after_restart(tmp_path):
    async def scenario():
        context = BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
        })
        app = declaration(Service([])).build(context)
        async with app.router.lifespan_context(app):
            await app.state.application.plugin_host.stop_runtime("example")

        restored = declaration(Service([])).build(context)
        status = restored.state.application.plugin_host.status("example")
        assert status.running is False
        assert status.binding_enabled is False

    asyncio.run(scenario())


def test_required_runtime_and_binding_are_protected():
    async def scenario():
        app = declaration(
            Service([]), runtime_required=True, binding_required=True
        ).build()
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
            with pytest.raises(RuntimeError, match="runtime is required"):
                await host.stop_runtime("example")
            with pytest.raises(RuntimeError, match="binding is required"):
                await host.set_binding("example", False)

    asyncio.run(scenario())


def test_binding_persistence_failure_rolls_back_worker_and_memory_state(tmp_path):
    async def scenario():
        bindings = []

        async def update_binding(name, enabled):
            bindings.append((name, enabled))

        app = declaration(Service([])).build(BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
            "plugin_binding_update": update_binding,
        }))
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host

            def fail(_payload):
                raise OSError("disk full")

            host.store.save = fail
            with pytest.raises(OSError, match="disk full"):
                await host.set_binding("example", False)
            assert host.status("example").binding_enabled is True
            assert bindings == [("example", False), ("example", True)]

    asyncio.run(scenario())


def test_runtime_persistence_failure_rolls_back_the_complete_transaction(tmp_path):
    async def scenario():
        events = []
        bindings = []
        private_agents = []
        app = declaration(Service(events)).build(BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
            "plugin_binding_update": (
                lambda name, enabled: bindings.append((name, enabled))
            ),
            "plugin_runtime_update": (
                lambda name, running: private_agents.append((name, running))
            ),
        }))
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host

            def fail(_payload):
                raise OSError("disk full")

            host.store.save = fail
            with pytest.raises(OSError, match="disk full"):
                await host.stop_runtime("example")
            status = host.status("example")
            assert status.running is True
            assert status.binding_enabled is True
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/plugin/value")).status_code == 200

        assert events == ["start", "stop", "start", "stop"]
        assert bindings == [("example", False), ("example", True)]
        assert private_agents == [
            ("example", True), ("example", False), ("example", True),
        ]

    asyncio.run(scenario())


def test_plugin_route_collision_fails_before_start():
    with pytest.raises(ValueError, match="duplicate application endpoint"):
        AgentApplication(
            name="Collision",
            version="1",
            primary_agent=AgentSpec(
                name="primary", description="Primary test agent.", session=SessionPolicy.durable()
            ),
            plugins=(Plugin(
                name="example",
                runtime=Extension(name="plugin_api", endpoints=(Api(),)),
            ),),
            extensions=(Extension(name="core_api", endpoints=(Api(),)),),
        ).build()


def test_failed_plugin_start_rolls_back_started_runtimes():
    async def scenario():
        first = []
        second = []
        app = AgentApplication(
            name="Rollback",
            version="1",
            primary_agent=AgentSpec(
                name="primary", description="Primary test agent.", session=SessionPolicy.durable()
            ),
            plugins=(
                Plugin(
                    name="first",
                    runtime=Extension(name="first_runtime", service=Service(first)),
                ),
                Plugin(
                    name="second",
                    runtime=Extension(
                        name="second_runtime", service=Service(second, fail=True)
                    ),
                ),
            ),
        ).build()
        with pytest.raises(RuntimeError, match="failed"):
            async with app.router.lifespan_context(app):
                pass
        assert first == ["start", "stop"]
        assert second == ["start"]

    asyncio.run(scenario())


def test_plugin_service_factory_receives_application_dependency_and_starts():
    events = []
    dependency = Service(events)
    received = []

    def create(dependency):
        received.append(dependency)
        return Service(events)

    app = AgentApplication(
        name="Factory",
        version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        extensions=(Extension(name="dependency", service=dependency),),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(
                name="feature_runtime",
                service_factory=create,
                requires=("dependency",),
            ),
        ),),
    ).build()

    from fastapi.testclient import TestClient
    with TestClient(app):
        assert received == [dependency]
        assert app.state.application.service("feature_runtime") is not None
        assert events == ["start", "start"]
    assert events == ["start", "start", "stop", "stop"]


def test_plugin_service_factory_can_depend_on_another_plugin_capability():
    events = []
    provider = Service(events)
    received = []

    def create(provider_runtime):
        received.append(provider_runtime)
        return Service(events)

    app = AgentApplication(
        name="Chained factories", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(
            Plugin(
                name="consumer",
                requires=(CapabilityRequirement(name="provider.api"),),
                runtime=Extension(
                    name="consumer_runtime", service_factory=create,
                    requires=("provider_runtime",),
                ),
            ),
            Plugin(
                name="provider", capabilities=(Capability(name="provider.api"),),
                runtime=Extension(name="provider_runtime", service=provider),
            ),
        ),
    ).build()

    from fastapi.testclient import TestClient
    with TestClient(app):
        assert received == [provider]
        assert app.state.application.service("consumer_runtime") is not None
    assert events == ["start", "start", "stop", "stop"]


def test_running_plugin_requires_running_capability_provider():
    declaration = AgentApplication(
        name="Dependencies",
        version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(
            Plugin(
                name="base", capabilities=(Capability(name="base.api"),),
                runtime_enabled=False, binding_enabled=False,
            ),
            Plugin(
                name="dependent",
                requires=(CapabilityRequirement(name="base.api"),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="requires stopped capability"):
        declaration.build()


def test_persisted_plugin_state_stops_dependent_without_its_provider(tmp_path):
    from the_framework.utils.persistence import MappingStore

    state_path = tmp_path / "plugins.json"
    MappingStore(state_path, field="plugins").save({
        "base": {"running": False, "binding_enabled": False},
        "dependent": {"running": True, "binding_enabled": False},
    })
    declaration = AgentApplication(
        name="Restored dependencies", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(
            Plugin(name="base", capabilities=(Capability(name="base.api"),)),
            Plugin(
                name="dependent",
                requires=(CapabilityRequirement(name="base.api"),),
            ),
            Plugin(name="independent"),
        ),
    )
    app = declaration.build(BuildContext({"plugin_state_path": state_path}))
    from fastapi.testclient import TestClient
    with TestClient(app):
        status = {item.name: item for item in app.state.application.plugin_host.status()}
        assert status["base"].running is False
        assert status["dependent"].running is False
        assert status["independent"].running is True
    saved = MappingStore(state_path, field="plugins").load()
    assert saved["dependent"]["running"] is False


def test_authenticated_plugin_api_controls_binding_and_runtime():
    async def scenario():
        application = declaration(Service([]))
        application = AgentApplication(
            name=application.name,
            version=application.version,
            primary_agent=application.primary_agent,
            plugins=application.plugins,
            security=Security(),
        )
        app = application.build()
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": "Bearer secret"},
            ) as client:
                listing = await client.get("/api/v1/plugins")
                assert listing.status_code == 200
                assert listing.json()["plugins"][0]["running"] is True
                binding = await client.put(
                    "/api/v1/plugins/example/binding", json={"enabled": False}
                )
                assert binding.status_code == 200
                runtime = await client.put(
                    "/api/v1/plugins/example/runtime", json={"running": False}
                )
                assert runtime.status_code == 200
                assert runtime.json()["running"] is False

    asyncio.run(scenario())
