import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from harness_core import (
    AgentApplication,
    AgentSpec,
    BuildContext,
    Extension,
    PluginSpec,
    SessionPolicy,
    endpoint,
)
from harness_core.server.api.endpoints import Principal


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
        plugins=(PluginSpec(
            name="example",
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
            plugins=(PluginSpec(
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
                PluginSpec(
                    name="first",
                    runtime=Extension(name="first_runtime", service=Service(first)),
                ),
                PluginSpec(
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
