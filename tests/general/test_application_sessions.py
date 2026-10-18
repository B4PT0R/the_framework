"""Independent sessions use real workers and one canonical state per session."""
import asyncio

from httpx import ASGITransport, AsyncClient

from the_framework import BuildContext
from the_framework.agent import Session
from the_framework.server.api.endpoints import Principal
from tests.fixtures.multi_session_app import application


class Security:
    async def authenticate(self, request):
        if request.headers.get("authorization") == "Bearer test":
            return Principal(id="test", scopes=frozenset({"*"}))

    async def authorize(self, principal, requirement, request):
        return True


async def handshake(socket):
    if socket.headers.get("authorization") == "Bearer test":
        return {"subprotocol": None}


def build(root):
    return application.build(
        "tests.fixtures.multi_session_app:application", BuildContext(data_root=root),
        security=Security(), handshake=handshake,
    )


def test_concurrent_workers_isolate_history_settings_bindings_and_resume(tmp_path):
    async def run():
        ids = []
        for generation in range(2):
            app = build(tmp_path)
            async with app.router.lifespan_context(app):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.post("/api/v1/sessions", json={})).status_code == 401
                    client.headers["authorization"] = "Bearer test"
                    if not generation:
                        replies = await asyncio.gather(*[
                            client.post("/api/v1/sessions", json={"agent": name})
                            for name in ("first", "second")
                        ])
                        assert [reply.status_code for reply in replies] == [201, 201], [reply.text for reply in replies]
                        ids.extend(reply.json()["id"] for reply in replies)
                    runtimes = [await app.state.application.open_session(session_id=key) for key in ids]
                    assert len({runtime.supervisor.process.pid for runtime in runtimes}) == 2
                    for index, (key, runtime) in enumerate(zip(ids, runtimes)):
                        prefix = f"/sessions/{key}/api/v1"
                        if not generation:
                            response = await client.patch(prefix + "/config", json={"feature": {"label": f"session-{index}"}})
                            assert response.status_code == 200, response.text
                            await runtime.apply_event("record", {"text": f"marker-{index}"})
                            if not index:
                                response = await client.put(prefix + "/plugins/feature/binding", json={"enabled": False})
                                assert response.status_code == 200, response.text
                        config = await client.get(prefix + "/config")
                        assert config.status_code == 200, config.text
                        assert config.json()["config"]["feature"]["label"] == f"session-{index}"
                        history = (await client.get(prefix + "/session")).json()
                        assert f"marker-{index}" in str(history)
                        assert f"marker-{1-index}" not in str(history)
                        state = (await client.get(prefix + "/state")).json()
                        assert state["state"]["feature"]["count"] == 1
                        plugins = (await client.get(prefix + "/plugins")).json()["plugins"]
                        assert plugins[0]["binding_enabled"] is bool(index)
                    assert (await client.get("/api/v1/session")).json()["turns"] == []
                    assert len((await client.get("/api/v1/sessions")).json()["sessions"]) == 3
            assert all(runtime.supervisor.process.returncode == 0 for runtime in runtimes)
        for index, key in enumerate(ids):
            session = Session.open(tmp_path / "sessions" / key / "session.json")
            assert session.configuration["feature"]["label"] == f"session-{index}"
            assert session.state["feature"]["count"] == 1
            assert session.plugins["feature"] is bool(index)
        assert not (tmp_path / "plugins.json").exists()
    asyncio.run(asyncio.wait_for(run(), 45))


def test_resident_session_is_discarded_on_close_and_does_not_cross_route_boundaries(tmp_path):
    async def run():
        app = build(tmp_path)
        async with app.router.lifespan_context(app):
            host = app.state.application
            runtime = await host.open_session(durable=False)
            key = runtime.ready.session_id
            await runtime.apply_event("record", {"text": "temporary"})
            assert not (tmp_path / "sessions" / key / "session.json").exists()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get(f"/sessions/{key}/api/v1/config")).status_code == 401
                client.headers["authorization"] = "Bearer test"
                assert (await client.get(f"/sessions/{key}/api/v1/session")).status_code == 200
                assert await host.close_session(key)
                assert (await client.get(f"/sessions/{key}/api/v1/session")).status_code == 404
            assert len(host.list_sessions()) == 1
            assert host.list_sessions()[0]["default"] is True
    asyncio.run(asyncio.wait_for(run(), 30))


def test_session_commit_failure_keeps_live_and_saved_settings_consistent(tmp_path, monkeypatch):
    from tests.fixtures.multi_session_app import Feature
    from the_framework import AgentSpec
    agent = AgentSpec(name="agent", session_mode="durable", extensions=(Feature,)).build_agent(tmp_path / "session.json")
    before = agent.session.path.read_text()
    def fail(self):
        raise OSError("disk full")
    monkeypatch.setattr(Session, "save", fail)
    import pytest
    with pytest.raises(OSError, match="disk full"):
        agent.update_config({"feature": {"label": "uncommitted"}})
    with pytest.raises(OSError, match="disk full"):
        agent.update_plugin_state("feature", {"count": 8})
    assert agent.configs.feature.label == "default"
    assert agent.states["feature"].count == 0
    assert agent.session.configuration["feature"]["label"] == "default"
    assert agent.session.state["feature"]["count"] == 0
    assert agent.session.path.read_text() == before


def test_another_worker_cannot_claim_the_same_session(tmp_path):
    from the_framework.server import WorkerSupervisor
    import pytest
    async def run():
        app = build(tmp_path)
        async with app.router.lifespan_context(app):
            duplicate = WorkerSupervisor(tmp_path / "session.json")
            with pytest.raises(RuntimeError, match="worker exited before ready"):
                await duplicate.start()
            assert any("already has an active worker" in line for line in duplicate.stderr)
            assert app.state.application.primary_session.supervisor.running
    asyncio.run(asyncio.wait_for(run(), 15))


def test_service_scopes_share_only_application_resources_and_fan_out_surface_events(tmp_path):
    from the_framework import Application, ServerExtension
    from the_framework.plugins.realtime import REALTIME

    changes = []

    class Shared:
        async def start(self):
            changes.append("start")

        async def stop(self):
            changes.append("stop")

    shared = Shared()
    spec = Application({**application,
        "plugins": (*application.plugins, REALTIME),
        "server_extensions": (
            ServerExtension(name="shared", service=shared),
            ServerExtension(name="private", scope="session", service_factory=lambda shared, context: context),
        ),
    })

    async def run():
        app = spec.build(
            "tests.fixtures.multi_session_app:application", BuildContext(data_root=tmp_path),
            security=Security(), handshake=handshake,
        )
        host = app.state.application
        async with app.router.lifespan_context(app):
            child = await host.open_session()
            child_app = host.session_apps[child.ready.session_id][0].state.session
            assert Application.current() is spec
            assert child.application is spec
            assert child.app.state.application is spec
            assert child.app.state.session is child
            assert child.context.application is spec
            assert child_app.service("shared") is shared
            assert child_app.service("private") is child.context
            assert host.service("private") is host.context
            assert child.context is not host.context
            assert child.context.require("session_path") != host.context.require("session_path")
            assert child_app.service("realtime").controller is not host.service("realtime").controller
            assert child_app.service("realtime").controller.runtime is child
            assert changes == ["start"]
            from contextlib import aclosing
            async with aclosing(host.primary_session.stream()) as primary_events, aclosing(child.stream()) as child_events:
                first = asyncio.create_task(anext(primary_events))
                second = asyncio.create_task(anext(child_events))
                await asyncio.sleep(0)
                await host.surface_notify({"surface": "main"})
                assert (await first)["type"] == "interface_refresh_requested"
                assert (await second)["type"] == "interface_refresh_requested"
            await host.close_session(child.ready.session_id)
            assert changes == ["start"]
        assert changes == ["start", "stop"]
    asyncio.run(asyncio.wait_for(run(), 20))


def test_invalid_session_requests_leave_no_workers_or_files(tmp_path):
    async def run():
        app = build(tmp_path)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"authorization": "Bearer test"}) as client:
                for payload in ({"session_id": "../escape"}, {"agent": "missing"}):
                    assert (await client.post("/api/v1/sessions", json=payload)).status_code == 422
                reply = await client.post("/api/v1/sessions", json={"session_id": "stable", "agent": "second"})
                assert reply.status_code == 201
                assert (await client.post("/api/v1/sessions", json={"session_id": "stable", "agent": "first"})).status_code == 422
                assert (await client.get("/sessions/unknown/api/v1/session")).status_code == 404
                assert list(app.state.application.session_apps) == ["stable"]
                assert [p.name for p in (tmp_path / "sessions").iterdir()] == ["stable"]
    asyncio.run(asyncio.wait_for(run(), 20))


def test_resetting_an_additional_session_preserves_its_route_and_settings(tmp_path):
    from the_framework.server import FileResetTransaction
    async def run():
        app = build(tmp_path)
        async with app.router.lifespan_context(app):
            host = app.state.application
            runtime = await host.open_session(session_id="workspace", agent="second")
            await runtime.apply_event("record", {"text": "old history"})
            await runtime.reset_runtime_state(
                transaction_factory=lambda path: FileResetTransaction(path.parent, ("session.json",)),
            )
            assert runtime.ready.session_id == "workspace"
            assert runtime.ready.name == "second"
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"authorization": "Bearer test"}) as client:
                response = await client.get("/sessions/workspace/api/v1/session")
                assert response.status_code == 200
                assert response.json()["turns"] == []
                state = await client.get("/sessions/workspace/api/v1/state")
                assert state.json()["state"]["feature"]["count"] == 1
    asyncio.run(asyncio.wait_for(run(), 20))


def test_cancelling_session_start_reaps_its_worker_and_removes_partial_data(tmp_path):
    import sys
    import pytest
    from the_framework.server.runtime.session import SessionRuntime
    supervisors = []
    def session_factory(supervisor, context):
        if context.get("session_owner") is not None:
            supervisor.command = [sys.executable, "-c", "import time; time.sleep(30)"]
            supervisors.append(supervisor)
        return SessionRuntime(supervisor)
    async def run():
        app = application.build(
            "tests.fixtures.multi_session_app:application", BuildContext(data_root=tmp_path),
            security=Security(), handshake=handshake, session_factory=session_factory,
        )
        host = app.state.application
        async with app.router.lifespan_context(app):
            opening = asyncio.create_task(host.open_session(session_id="cancelled"))
            while not supervisors or not supervisors[0].running:
                await asyncio.sleep(0.01)
            opening.cancel()
            with pytest.raises(asyncio.CancelledError):
                await opening
            assert supervisors[0].process.returncode is not None
            assert not (tmp_path / "sessions/cancelled").exists()
            assert not host.session_apps
            assert host.primary_session.supervisor.running
        with pytest.raises(RuntimeError, match="not running"):
            await host.open_session()
    asyncio.run(asyncio.wait_for(run(), 15))
