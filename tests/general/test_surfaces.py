import asyncio
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from the_framework.server.composition.application import _assemble_server
from the_framework import (
    Application,
    AgentSpec,
    BuildContext,
    ClientSurface,
    ServerExtension,
    Plugin,
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
    return Application(
        name="Surface test",
        version="1",
        agents=(AgentSpec(
            name="primary", description="Primary test agent."
        ),),
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
        app = _assemble_server(declaration(source), context=context)
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
            assert preview.text.endswith("<main>first</main>")
            assert 'framework-client-role' in preview.text

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
            assert status.json()["surfaces"][0]["building"] is False
            # Other clients can see and cancel builds after their UI reconnects.
            service.building.add("main")
            try:
                status = await client.get("/api/v1/surfaces", headers=headers)
                assert status.json()["surfaces"][0]["building"] is True
            finally:
                service.building.remove("main")

        assert [item["type"] for item in notifications] == [
            "surface_published", "surface_published", "surface_rolled_back",
        ]

        restored = _assemble_server(declaration(source), context=BuildContext({
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
        app = _assemble_server(declaration(source, timeout=0.1), context=BuildContext({
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
        app = _assemble_server(declaration(source), context=BuildContext({
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
        app = _assemble_server(declaration(source, retain=2), context=BuildContext({
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
        app = _assemble_server(declaration(source), context=BuildContext({
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
        Application(
            name="Nested", version="1",
            agents=(AgentSpec(
                name="primary", description="Primary.",
            ),),
            surfaces=(first, nested),
            security=Security(),
        ).compile()

    with pytest.raises(ValueError, match="shadowed by a mount"):
        _assemble_server(Application(
            name="Shadowed", version="1",
            agents=(AgentSpec(
                name="primary", description="Primary.",
            ),),
            plugins=(Plugin(
                name="example",
                server_extensions=ServerExtension(
                    name="example_runtime", endpoints=(shadowed_plugin_endpoint,),
                ),
            ),),
            surfaces=(first,),
            security=Security(),
        ), context=BuildContext({"surface_root": tmp_path / "runtime"}))


def test_declared_plugin_bootstrap_endpoint_may_precede_its_surface_mount(tmp_path):
    @endpoint("get", "/ui/bootstrap", authenticated=False)
    def bootstrap():
        return {"ready": True}

    source = source_tree(tmp_path)
    specification = Application(
        name="Surface bootstrap", version="1",
        agents=(AgentSpec(name="primary"),),
        plugins=(Plugin(
            name="hosted_ui", server_extensions=ServerExtension(
                name="hosted_ui_runtime", endpoints=(bootstrap,),
            ),
        ),),
        surfaces=(ClientSurface(
            name="main", source=source, build=("true",), artifact="build",
            routes=("/ui",), server_routes=("/ui/bootstrap",),
        ),),
        security=Security(),
    )
    app = _assemble_server(specification, context=BuildContext({
        "surface_root": tmp_path / "runtime",
    }))

    async def scenario():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/ui/bootstrap")
            schema = (await client.get("/openapi.json")).json()
        assert response.json() == {"ready": True}
        assert "/ui/bootstrap" in schema["paths"]

    asyncio.run(scenario())


def test_surface_server_route_must_belong_to_surface(tmp_path):
    source = source_tree(tmp_path)
    with pytest.raises(ValueError, match="surface server routes"):
        ClientSurface(
            name="main", source=source, build=("true",), artifact="build",
            routes=("/ui",), server_routes=("/api/v1/unrelated",),
        )


def test_initial_artifacts_are_adopted_without_an_agent_runtime_and_never_replace_publication(tmp_path):
    from fastapi.testclient import TestClient

    source = source_tree(tmp_path)
    compiled = source / "build"
    compiled.mkdir()
    (compiled / "index.html").write_text("Initial bundle")
    specification = declaration(source)
    context = BuildContext(surface_root=tmp_path / "runtime")
    app = _assemble_server(specification, context=context)
    with TestClient(app) as client:
        assert client.get("/ui/").text == "Initial bundle"
        selected = app.state.application.surface_service.active_path("main").name
    (compiled / "index.html").write_text("Unreviewed replacement")
    restored = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime"))
    with TestClient(restored) as client:
        assert client.get("/ui/").text == "Initial bundle"
        assert restored.state.application.surface_service.active_path("main").name == selected


def test_missing_initial_artifact_leaves_server_available_and_can_be_repaired(tmp_path):
    from fastapi.testclient import TestClient

    app = _assemble_server(declaration(source_tree(tmp_path)), context=BuildContext(surface_root=tmp_path / "runtime"))
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 200
        assert client.get("/ui/").status_code == 503
        headers = {"Authorization": "Bearer secret"}
        assert client.post("/api/v1/surfaces/main/build", headers=headers).status_code == 200
        assert client.post("/api/v1/surfaces/main/publish", headers=headers).status_code == 200
        assert client.get("/ui/").text == "<main>first</main>"


def test_preview_survives_restart_and_failure_without_becoming_published(tmp_path):
    async def scenario():
        source = source_tree(tmp_path)
        specification = declaration(source)
        service = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service
        first = await service.build("main")
        await service.publish("main")
        (source / "content.txt").write_text("Reviewed later")
        candidate = await service.build("main")
        (source / "behavior.txt").write_text("fail")
        with pytest.raises(RuntimeError, match="build failed"):
            await service.build("main")
        restored = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service
        assert restored.status()["surfaces"][0]["candidate"] == candidate.release
        assert restored.active_path("main").name == first.release
        assert (restored.preview_path("main") / "index.html").read_text() == "<main>Reviewed later</main>"
        await restored.publish("main")
        assert restored.active_path("main").name == candidate.release
        await restored.rollback("main")
        assert restored.active_path("main").name == first.release

    asyncio.run(scenario())


def test_interrupted_release_copy_is_not_visible_and_build_can_be_retried(tmp_path, monkeypatch):
    import shutil
    import the_framework.server.clients.surfaces as surfaces

    async def scenario():
        source = source_tree(tmp_path)
        service = _assemble_server(declaration(source), context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service
        copy = shutil.copytree

        def interrupted(source, target, *args, **kwargs):
            if str(target).endswith("/content"):
                target.mkdir()
                (target / "index.html").write_text("Incomplete")
                raise OSError("copy interrupted")
            return copy(source, target, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(surfaces.shutil, "copytree", interrupted)
            with pytest.raises(OSError, match="copy interrupted"):
                await service.build("main")
        assert service.active_path("main") is None
        assert service.candidates == {}
        assert list((service.releases_root / "main").iterdir()) == []
        assert not service.building
        await service.build("main")
        await service.publish("main")
        assert (service.active_path("main") / "index.html").read_text() == "<main>first</main>"

    asyncio.run(scenario())


def test_failed_build_preparation_releases_the_build_slot(tmp_path):
    async def scenario():
        service = _assemble_server(declaration(source_tree(tmp_path)), context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service

        def fail(_payload):
            raise RuntimeError("progress unavailable")

        service.progress = fail
        with pytest.raises(RuntimeError, match="progress unavailable"):
            await service.build("main")
        assert not service.building
        service.progress = None
        await service.build("main")

    asyncio.run(scenario())


def test_missing_active_release_recovers_previous_before_any_source_bundle(tmp_path):
    import shutil

    async def scenario():
        source = source_tree(tmp_path)
        specification = declaration(source)
        service = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service
        first = await service.build("main")
        await service.publish("main")
        (source / "content.txt").write_text("second")
        await service.build("main")
        await service.publish("main")
        shutil.rmtree(service.active_path("main"))
        compiled = source / "build"
        compiled.mkdir()
        (compiled / "index.html").write_text("Unreviewed bundle")
        restored = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime")).state.application.surface_service
        await restored.initialize()
        assert restored.active_path("main").name == first.release
        assert restored.state["main"]["previous"] is None

    asyncio.run(scenario())


def test_device_surfaces_have_independent_initial_bundles_previews_and_rollbacks(tmp_path):
    from fastapi.testclient import TestClient

    surfaces = []
    for name in ("desktop", "mobile", "quest"):
        root = tmp_path / name
        root.mkdir()
        source = source_tree(root)
        (source / "build").mkdir()
        (source / "build/index.html").write_text(f"Initial {name}")
        (source / "content.txt").write_text(f"Edited {name}")
        surfaces.append(ClientSurface(
            name=name, source=source, build=(sys.executable, "builder.py", "{artifact}"),
            artifact="build", routes=(f"/{name}",),
        ))
    specification = Application(
        name="Multiple devices", version="1",
        agents=(AgentSpec(name="primary"),),
        surfaces=surfaces, security=Security(),
    )
    app = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime"))
    headers = {"Authorization": "Bearer secret"}
    with TestClient(app) as client:
        for name in ("desktop", "mobile", "quest"):
            assert client.get(f"/{name}/").text == f"Initial {name}"
        before = client.get("/api/v1/surfaces", headers=headers).json()["surfaces"]
        built = client.post("/api/v1/surfaces/mobile/build", headers=headers).json()
        assert client.get(built["preview"]).status_code == 401
        assert client.get(built["preview"], headers=headers).text.endswith("<main>Edited mobile</main>")
        assert client.get("/mobile/").text == "Initial mobile"
        assert client.post("/api/v1/surfaces/mobile/publish", headers=headers).status_code == 200
        assert client.get("/mobile/").text == "<main>Edited mobile</main>"
        assert client.post("/api/v1/surfaces/mobile/rollback", headers=headers).status_code == 200
        assert client.get("/mobile/").text == "Initial mobile"
        after = client.get("/api/v1/surfaces", headers=headers).json()["surfaces"]
        for name in ("desktop", "quest"):
            assert next(item for item in after if item["name"] == name) == next(item for item in before if item["name"] == name)
            assert client.get(f"/{name}/").text == f"Initial {name}"
    restored = _assemble_server(specification, context=BuildContext(surface_root=tmp_path / "runtime"))
    with TestClient(restored) as client:
        assert client.get("/mobile/").text == "Initial mobile"
        assert client.get("/quest/").text == "Initial quest"
        assert client.get("/desktop/").text == "Initial desktop"


def test_active_surface_revalidates_cached_entry_after_publication(tmp_path):
    """A reopened WebView must not reuse a heuristically fresh entry page."""
    async def scenario():
        source = source_tree(tmp_path)
        app = _assemble_server(declaration(source), context=BuildContext({
            "surface_root": tmp_path / "runtime",
        }))
        service = app.state.application.surface_service
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await service.build("main")
            await service.publish("main")
            for route in ("/ui/", "/ui/index.html", "/remote-ui/", "/quest-ui/"):
                first = await client.get(route)
                assert first.status_code == 200
                assert first.headers["cache-control"] == "no-cache"
                cached = await client.get(route, headers={"If-None-Match": first.headers["etag"]})
                assert cached.status_code == 304
                assert cached.headers["cache-control"] == "no-cache"
            old_etag = first.headers["etag"]
            (source / "content.txt").write_text("new release")
            await service.build("main")
            await service.publish("main")
            updated = await client.get("/ui/", headers={"If-None-Match": old_etag})
            assert updated.status_code == 200
            assert updated.text == "<main>new release</main>"
            assert updated.headers["cache-control"] == "no-cache"
            await service.rollback("main")
            restored = await client.get("/ui/", headers={"If-None-Match": updated.headers["etag"]})
            assert restored.status_code == 200
            assert restored.text == "<main>first</main>"
            assert restored.headers["cache-control"] == "no-cache"
    asyncio.run(scenario())
