import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware

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


def test_binding_changes_do_not_stop_runtime_and_persist(tmp_path):
    async def scenario():
        events = []
        bindings = []

        async def update_binding(name, enabled):
            bindings.append((name, enabled))

        context = BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
            "plugin_binding_update": update_binding,
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
                assert host.status("example").binding_enabled is False
                assert host.status("example").binding_available is True
                assert host.status("example").running is True
                await host.set_binding("example", True)
        assert bindings == [
            ("example", False),
            ("example", True),
        ]
        assert events == ["start", "stop"]

        restored = declaration(Service([])).build(context)
        status = restored.state.application.plugin_host.status("example")
        assert status.running is True
        assert status.binding_enabled is True

    asyncio.run(scenario())


def test_concurrent_binding_changes_are_serialized_and_persisted(tmp_path):
    async def scenario():
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        updates = []

        async def update_binding(name, enabled):
            updates.append((name, enabled))
            if not enabled:
                first_started.set()
                await release_first.wait()

        context = BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
            "plugin_binding_update": update_binding,
        })
        app = declaration(Service([])).build(context)
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
            disable = asyncio.create_task(host.set_binding("example", False))
            await first_started.wait()
            enable = asyncio.create_task(host.set_binding("example", True))
            await asyncio.sleep(0)
            release_first.set()
            await asyncio.gather(disable, enable)
            assert host.status("example").binding_enabled is True

        assert updates == [("example", False), ("example", True)]
        restored = declaration(Service([])).build(context)
        assert restored.state.application.plugin_host.status("example").binding_enabled is True

    asyncio.run(scenario())


def test_plugin_owns_multiple_ordered_server_components_and_static_routes():
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
        assert app.state.application.plugin_host.status("feature").binding_available is False
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
            with pytest.raises(RuntimeError, match="no agent binding"):
                await host.set_binding("feature", True)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                assert (await client.get("/plugin/value")).status_code == 200
        assert events == [
            "start:store", "start:api", "stop:api", "stop:store",
        ]

    asyncio.run(scenario())


def test_plugin_runtime_installs_mount_middleware_and_openapi_on_main_app():
    class TraceMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Plugin-Trace"] = "installed"
            return response

    assets = FastAPI()

    @assets.get("/item")
    def item():
        return {"asset": True}

    @endpoint("get", "/core/value", authenticated=False)
    def core_value():
        return {"core": True}

    app = AgentApplication(
        name="Vertical surface", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(
                name="feature_runtime", endpoints=(Api(),),
                middleware=((TraceMiddleware, {}),),
                mounts=(("/plugin/assets", assets, "feature-assets"),),
            ),
        ),),
        extensions=(Extension(name="core_api", endpoints=(core_value,)),),
    ).build()

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            route = await client.get("/plugin/value")
            core_route = await client.get("/core/value")
            mounted = await client.get("/plugin/assets/item")
            schema = (await client.get("/openapi.json")).json()
        assert route.status_code == 200
        assert mounted.json() == {"asset": True}
        assert route.headers["X-Plugin-Trace"] == "installed"
        assert core_route.headers["X-Plugin-Trace"] == "installed"
        assert mounted.headers["X-Plugin-Trace"] == "installed"
        assert "/plugin/value" in schema["paths"]
        assert "/core/value" in schema["paths"]

    asyncio.run(scenario())


def test_plugin_mount_collision_is_rejected_before_start():
    app = FastAPI()
    declaration = AgentApplication(
        name="Mount collision", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(
                name="feature_runtime", mounts=(("/plugin", app, None),),
            ),
        ),),
        extensions=(Extension(name="api", endpoints=(Api(),)),),
    )
    with pytest.raises(ValueError, match="shadowed by a mount"):
        declaration.build()

    nested = AgentApplication({**declaration,
        "extensions": (Extension(
            name="assets", mounts=(("/plugin/assets", app, None),),
        ),),
    })
    with pytest.raises(ValueError, match="overlapping application mount"):
        nested.build()


def test_application_service_can_depend_on_plugin_owned_service():
    events = []
    provider = Service(events)
    dependent = Service(events)
    app = AgentApplication(
        name="Cross-boundary lifecycle",
        version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(Plugin(
            name="provider",
            runtime=Extension(name="provider_service", service=provider),
        ),),
        extensions=(Extension(
            name="dependent_service",
            service_factory=lambda provider_service: (
                dependent if provider_service is provider else None
            ),
            requires=("provider_service",),
        ),),
    ).build()

    async def scenario():
        async with app.router.lifespan_context(app):
            assert app.state.application.service("provider_service") is provider
            assert app.state.application.service("dependent_service") is dependent
            assert events == ["start", "start"]
        assert events == ["start", "start", "stop", "stop"]
        assert app.state.application.services.snapshot() == {}

    asyncio.run(scenario())


def test_capability_dependency_orders_plugin_services_without_extension_edge():
    events = []

    class NamedService:
        def __init__(self, name):
            self.name = name

        def start(self):
            events.append(f"start:{self.name}")

        def stop(self):
            events.append(f"stop:{self.name}")

    app = AgentApplication(
        name="Capability startup order",
        version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(
            Plugin(
                name="consumer",
                requires=(CapabilityRequirement(name="provider.api"),),
                runtime=Extension(name="consumer_service", service=NamedService("consumer")),
            ),
            Plugin(
                name="provider",
                capabilities=(Capability(name="provider.api"),),
                runtime=Extension(name="provider_service", service=NamedService("provider")),
            ),
        ),
    ).build()

    async def scenario():
        async with app.router.lifespan_context(app):
            pass
        assert events == [
            "start:provider", "start:consumer",
            "stop:consumer", "stop:provider",
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


def test_binding_disable_does_not_reenable_after_restart(tmp_path):
    async def scenario():
        context = BuildContext({
            "plugin_state_path": tmp_path / "plugins.json",
        })
        app = declaration(Service([])).build(context)
        async with app.router.lifespan_context(app):
            await app.state.application.plugin_host.set_binding("example", False)

        restored = declaration(Service([])).build(context)
        status = restored.state.application.plugin_host.status("example")
        assert status.running is True
        assert status.binding_enabled is False

    asyncio.run(scenario())


def test_required_binding_is_protected():
    async def scenario():
        app = declaration(
            Service([]), binding_required=True
        ).build()
        async with app.router.lifespan_context(app):
            host = app.state.application.plugin_host
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


def test_cross_plugin_service_edge_requires_a_public_capability():
    consumer = Plugin(
        name="consumer",
        runtime=Extension(name="consumer_runtime", requires=("provider_runtime",)),
    )
    provider = Plugin(
        name="provider",
        capabilities=(Capability(name="provider.api"),),
        runtime=Extension(name="provider_runtime", service=object()),
    )
    spec = AgentApplication(
        name="Undeclared dependency", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(consumer, provider),
    )
    with pytest.raises(ValueError, match="without a versioned capability requirement"):
        spec.build()

    optional = Plugin({
        **consumer,
        "optional_requires": (CapabilityRequirement(name="provider.api"),),
    })
    spec = AgentApplication({**spec, "plugins": (optional, provider)})
    assert spec.build().state.application.plan.extension_order == (
        "provider_runtime", "consumer_runtime",
    )


def test_declared_plugin_requires_declared_capability_provider():
    declaration = AgentApplication(
        name="Dependencies",
        version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(
            Plugin(
                name="base", binding_enabled=False,
            ),
            Plugin(
                name="dependent",
                requires=(CapabilityRequirement(name="base.api"),),
            ),
        ),
    )
    with pytest.raises(ValueError, match="requires unavailable capability"):
        declaration.build()


def test_legacy_stopped_runtime_requires_explicit_startup_migration(tmp_path):
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
    with pytest.raises(ValueError, match="legacy plugin state has a stopped runtime: base"):
        declaration.build(BuildContext({"plugin_state_path": state_path}))


def test_authenticated_plugin_api_controls_only_binding():
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
                assert runtime.status_code == 404
                assert (await client.get("/plugin/value")).status_code == 200

    asyncio.run(scenario())


def test_factory_service_exposes_its_decorated_routes_without_endpoint_copy():
    events = []

    class FeatureService(Service):
        def __init__(self, dependency):
            super().__init__(events)
            self.dependency = dependency

        @endpoint("get", "/feature/from-service", authenticated=False)
        def value(self):
            """Return the service dependency through its own HTTP route."""
            return {"value": self.dependency}

    definition = AgentApplication(
        name="Factory routes", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        extensions=(Extension(name="dependency", service="ready"),),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(
                name="feature_runtime",
                service_factory=lambda dependency: FeatureService(dependency),
                endpoints="service",
                requires=("dependency",),
            ),
        ),),
    )
    app = definition.build()
    service = app.state.application.plan.plugin_extensions["feature"][0].service
    assert isinstance(service, FeatureService)
    assert app.state.application.plan.plugin_extensions["feature"][0].endpoints == (service,)

    async def scenario():
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/feature/from-service")
                assert response.status_code == 200
                assert response.json() == {"value": "ready"}
                schema = (await client.get("/openapi.json")).json()
                assert "/feature/from-service" in schema["paths"]
    asyncio.run(scenario())
    assert events == ["start", "stop"]


def test_prebuilt_plugin_service_exposes_decorated_routes_without_endpoint_copy():
    app = AgentApplication(
        name="Prebuilt routes", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(name="feature_runtime", service=Api(), endpoints="service"),
        ),),
    ).build()

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/plugin/value")
            assert response.status_code == 200
            assert response.json() == {"value": 1}

    asyncio.run(scenario())


def test_service_routes_require_explicit_opt_in():
    app = AgentApplication(
        name="Private service", version="1",
        primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
        plugins=(Plugin(
            name="feature",
            runtime=Extension(name="feature_runtime", service=Api()),
        ),),
    ).build()

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/plugin/value")).status_code == 404

    asyncio.run(scenario())
    with pytest.raises(ValueError, match="without a decorated service"):
        AgentApplication(
            name="Invalid service", version="1",
            primary_agent=AgentSpec(name="primary", session=SessionPolicy.durable()),
            plugins=(Plugin(
                name="feature",
                runtime=Extension(
                    name="feature_runtime", service=object(), endpoints="service",
                ),
            ),),
        ).build()
