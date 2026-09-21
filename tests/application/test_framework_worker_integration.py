"""Exercise declarative HTTP composition with the real generic worker."""

import asyncio
import sys

from httpx import ASGITransport, AsyncClient

from harness_core.agent import AgentSpec, SessionPolicy
from harness_core.agent.models.responses import Message
from harness_core.agent.context.session import Session
from harness_core.server import (
    AgentApplication,
    CanonicalAgentApi,
    Extension,
    WorkerSupervisor,
    build_application,
)
from harness_core.server.runtime.application import ApplicationRuntime
from harness_core.server.api.endpoints import Principal


def test_independent_application_preserves_session_across_server_recreation(tmp_path):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.append(Message(role="user", content=[{
        "type": "input_text", "text": "Independent application marker",
    }]))

    class Security:
        async def authenticate(self, request):
            if request.headers.get("authorization") == "Bearer test-secret":
                return Principal(id="tester", scopes=frozenset({"session:read", "config:read", "config:write"}))

        async def authorize(self, principal, requirement, request):
            return requirement is None or requirement["scope"] in principal.scopes

    async def run():
        histories = []
        processes = []
        for iteration in range(2):
            runtime = ApplicationRuntime(WorkerSupervisor(path))
            api = CanonicalAgentApi(runtime)
            app = build_application(AgentApplication(
                name="Independent Test", version="1",
                primary_agent=AgentSpec(
                    name="primary", description="Primary test agent.",
                    session=SessionPolicy.durable(),
                ),
                security=Security(), extensions=(
                    Extension(name="runtime", service=runtime),
                    Extension(name="api", endpoints=(api.session, api.config, api.update_config),
                              requires=("runtime",)),
                ),
            ))
            async with app.router.lifespan_context(app):
                assert runtime.ready.session_id == session.id
                processes.append(runtime.supervisor.process)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    assert (await client.get("/api/v1/session")).status_code == 401
                    client.headers["Authorization"] = "Bearer test-secret"
                    history = await client.get("/api/v1/session")
                    assert history.status_code == 200
                    histories.append({
                        key: value for key, value in history.json().items()
                        if key != "request_id"
                    })
                    if iteration == 0:
                        updated = await client.patch("/api/v1/config", json={
                            "reasoning": {"effort": "medium"},
                        })
                        assert updated.status_code == 200, updated.text
                    config = await client.get("/api/v1/config")
                    assert config.status_code == 200
                    if iteration == 0:
                        assert config.json()["config"]["reasoning"]["effort"] == "medium"
            assert processes[-1].returncode == 0
            assert runtime.relay_task is None
            assert runtime.fleet_result_task is None
            assert not runtime.responses
        assert histories[0] == histories[1]
        assert "Independent application marker" in str(histories[1])

    asyncio.run(run())


def test_generic_worker_builds_a_declared_private_agent(tmp_path):
    async def run():
        session = tmp_path / "private-agent.json"
        supervisor = WorkerSupervisor(
            session,
            command=[
                sys.executable,
                "-m",
                "harness_core.agent.runtime.worker_process",
                "--session",
                str(session),
                "--application",
                "starter.application:application",
                "--agent",
                "memory.jiminy",
            ],
        )
        ready = await supervisor.start()
        try:
            assert ready.name == "memory.jiminy"
            assert ready.plugins == [{"name": "memory_curator", "activated": True}]
        finally:
            await supervisor.stop()
        assert supervisor.process.returncode == 0

    asyncio.run(run())
