import asyncio
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from the_framework import (
    AgentApplication,
    AgentSpec,
    BuildContext,
    ClientSurface,
    Extension,
    Plugin,
    SessionPolicy,
    endpoint,
)
from the_framework.server.api.endpoints import Principal


class Security:
    async def authenticate(self, request):
        if request.headers.get("authorization") != "Bearer secret":
            return None
        return Principal(id="test", scopes=frozenset({
            "surfaces:read", "surfaces:write", "surfaces:preview",
        }))

    async def authorize(self, principal, requirement, _request):
        return requirement["scope"] in principal.scopes


@endpoint("get", "/ui/plugin", authenticated=False)
def shadowed_plugin_endpoint():
    return {"shadowed": True}


def source_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "content.txt").write_text("first", encoding="utf-8")
    (source / "behavior.txt").write_text("build", encoding="utf-8")
    (source / "builder.py").write_text(
        """\
import sys
import time
from pathlib import Path

behavior = Path("behavior.txt").read_text(encoding="utf-8").strip()
if behavior == "fail":
    raise SystemExit(17)
if behavior == "sleep":
    time.sleep(30)
artifact = Path(sys.argv[1])
artifact.mkdir(parents=True, exist_ok=True)
content = Path("content.txt").read_text(encoding="utf-8")
(artifact / "index.html").write_text(f"<main>{content}</main>", encoding="utf-8")
""",
        encoding="utf-8",
    )
    return source


def declaration(source, *, timeout=10, retain=3):
    return AgentApplication(
        name="Surface test",
        version="1",
        primary_agent=AgentSpec(
            name="primary", description="Primary test agent.", session=SessionPolicy.durable()
        ),
        surfaces=(ClientSurface(
            name="main",
            source=source,
            build=(sys.executable, "builder.py", "{artifact}"),
            artifact="build",
            routes=("/ui", "/remote-ui", "/quest-ui"),
            retain_releases=retain,
            timeout_seconds=timeout,
        ),),
        security=Security(),
    )


def test_surface_build_preview_publish_rollback_and_restore(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        notifications = []
        context = BuildContext({
            "surface_root": tmp_path / "runtime",
            "surface_notify": notifications.append,
        })
        app = declaration(source).build(context)
        service = app.state.application.surface_service
        headers = {"Authorization": "Bearer secret"}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            unpublished = await client.get("/ui/")
            assert unpublished.status_code == 503

            first = await client.post("/api/v1/surfaces/main/build", headers=headers)
            assert first.status_code == 200
            first_id = first.json()["release"]
            assert (await client.get(first.json()["preview"])).status_code == 401
            preview = await client.get(first.json()["preview"], headers=headers)
            assert preview.text == "<main>first</main>"

            published = await client.post(
                "/api/v1/surfaces/main/publish",
                json={"release": first_id},
                headers=headers,
            )
            assert published.status_code == 200
            for route in ("/ui/", "/remote-ui/", "/quest-ui/"):
                assert (await client.get(route)).text == "<main>first</main>"

            (source / "content.txt").write_text("second", encoding="utf-8")
            second = await service.build("main")
            await service.publish("main", second.release)
            assert (await client.get("/ui/")).text == "<main>second</main>"

            rollback = await client.post(
                "/api/v1/surfaces/main/rollback", headers=headers
            )
            assert rollback.status_code == 200
            assert rollback.json()["release"] == first_id
            assert (await client.get("/ui/")).text == "<main>first</main>"

            status = await client.get("/api/v1/surfaces", headers=headers)
            assert status.json()["surfaces"][0]["active"] == first_id

        assert [item["type"] for item in notifications] == [
            "surface_published", "surface_published", "surface_rolled_back",
        ]

        restored = declaration(source).build(BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        assert restored.state.application.surface_service.active_path("main").name == first_id

        schema = app.openapi()
        assert "/api/v1/surfaces/{name}/build" in schema["paths"]
        operation = schema["paths"]["/api/v1/surfaces/{name}/build"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]

    asyncio.run(scenario())


def test_failed_and_timed_out_builds_do_not_replace_the_active_release(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        app = declaration(source, timeout=0.1).build(BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        service = app.state.application.surface_service
        first = await service.build("main")
        await service.publish("main", first.release)

        (source / "behavior.txt").write_text("fail", encoding="utf-8")
        with pytest.raises(RuntimeError, match="surface build failed"):
            await service.build("main")
        assert service.active_path("main").name == first.release

        (source / "behavior.txt").write_text("sleep", encoding="utf-8")
        with pytest.raises(RuntimeError, match="timed out"):
            await service.build("main")
        assert service.active_path("main").name == first.release

    asyncio.run(scenario())


def test_build_can_be_cancelled_and_bootstrap_adopts_existing_artifact(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        (source / "behavior.txt").write_text("sleep", encoding="utf-8")
        app = declaration(source).build(BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        service = app.state.application.surface_service
        task = asyncio.create_task(service.build("main"))
        for _ in range(100):
            if "main" in service.processes:
                break
            await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="already running"):
            await service.build("main")
        assert await service.cancel("main") is True
        with pytest.raises(RuntimeError, match="surface build failed"):
            await task

        existing = tmp_path / "existing"
        existing.mkdir()
        (existing / "index.html").write_text("<main>existing</main>", encoding="utf-8")
        adopted = service.bootstrap("main", existing)
        assert service.active_path("main") == service.release_path(adopted.surface, adopted.release)
        assert service.bootstrap("main", existing).release == adopted.release

    asyncio.run(scenario())


def test_surface_retention_keeps_active_and_rollback_releases(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        app = declaration(source, retain=2).build(BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        service = app.state.application.surface_service
        releases = []
        for content in ("one", "two", "three"):
            (source / "content.txt").write_text(content, encoding="utf-8")
            release = await service.build("main")
            releases.append(release.release)
            await service.publish("main", release.release)
        assert not service.release_path("main", releases[0]).exists()
        assert service.release_path("main", releases[1]).is_dir()
        assert service.release_path("main", releases[2]).is_dir()

    asyncio.run(scenario())


def test_surface_api_returns_controlled_errors(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        app = declaration(source).build(BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        headers = {"Authorization": "Bearer secret"}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            missing = await client.post(
                "/api/v1/surfaces/missing/build", headers=headers
            )
            rollback = await client.post(
                "/api/v1/surfaces/main/rollback", headers=headers
            )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "surface_not_found"
        assert rollback.status_code == 409
        assert rollback.json()["error"]["code"] == "surface_rollback_unavailable"

    asyncio.run(scenario())


def test_surface_mounts_reject_nested_routes_and_shadowed_plugin_endpoints(tmp_path):
    source = source_tree(tmp_path)
    first = ClientSurface(
        name="main", source=source, build=("true",), artifact="build", routes=("/ui",),
    )
    nested = ClientSurface(
        name="nested", source=source, build=("true",), artifact="build", routes=("/ui/plugin",),
    )
    with pytest.raises(ValueError, match="overlapping client surface route"):
        AgentApplication(
            name="Nested", version="1",
            primary_agent=AgentSpec(
                name="primary", description="Primary.", session=SessionPolicy.durable(),
            ),
            surfaces=(first, nested),
            security=Security(),
        ).compile()

    with pytest.raises(ValueError, match="shadowed by a mount"):
        AgentApplication(
            name="Shadowed", version="1",
            primary_agent=AgentSpec(
                name="primary", description="Primary.", session=SessionPolicy.durable(),
            ),
            plugins=(Plugin(
                name="example",
                runtime=Extension(
                    name="example_runtime", endpoints=(shadowed_plugin_endpoint,),
                ),
            ),),
            surfaces=(first,),
            security=Security(),
        ).build(BuildContext({"surface_root": tmp_path / "runtime"}))
