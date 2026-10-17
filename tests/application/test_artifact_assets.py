import asyncio

import pytest
from fastapi.testclient import TestClient
from PIL import Image, UnidentifiedImageError

from starter.server import create_app
from the_framework.plugins.artifacts.server import ArtifactAssets
from tests.application.test_starter import FakeRuntime


def test_artifact_asset_survives_source_deletion_and_service_restart(tmp_path):
    source = tmp_path / "résultat #1.png"
    Image.new("RGB", (24, 16), "teal").save(source)
    original = source.read_bytes()
    root = tmp_path / "app"
    payload = asyncio.run(ArtifactAssets(root / "artifacts").handle("publish", {
        "kind": "image", "payload": {"path": str(source), "title": "Résultat"},
    }))
    source.unlink()
    assert "path" not in payload
    assert "%23" in payload["src"]
    app = create_app(root, token="test", origin="http://testserver", runtime=FakeRuntime())
    with TestClient(app) as client:
        assert client.get(payload["src"]).status_code == 401
        client.headers["authorization"] = "Bearer test"
        response = client.get(payload["src"])
        assert response.content == original
        assert response.headers["content-type"] == "image/png"
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/v1/artifacts/absent").status_code == 404


def test_assets_are_immutable_and_invalid_images_do_not_leave_copies(tmp_path):
    source = tmp_path / "report.html"
    source.write_text("<h1>report</h1>")
    assets = ArtifactAssets(tmp_path / "assets")
    first = assets.publish("file", {"path": str(source)})
    source.write_text("updated")
    second = assets.publish("file", {"path": str(source)})
    assert first["src"] != second["src"]
    assert len(list(assets.root.iterdir())) == 2
    with pytest.raises(UnidentifiedImageError):
        assets.publish("image", {"path": str(source)})
    assert len(list(assets.root.iterdir())) == 2


def test_asset_route_rejects_symlink_escape(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (root / "linked").symlink_to(outside)
    from the_framework.server.api.endpoints import HttpError
    with pytest.raises(HttpError):
        asyncio.run(ArtifactAssets(root).content("linked"))
