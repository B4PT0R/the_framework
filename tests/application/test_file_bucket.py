import asyncio

import pytest
from fastapi.testclient import TestClient

from starter.server import create_app
from the_framework.server.api.bucket import FileBucket
from the_framework.server.api.endpoints import HttpError
from the_framework.plugins.artifacts.server import ArtifactAssets
from tests.application.test_starter import FakeRuntime


def test_uploads_and_published_files_share_one_authenticated_bucket(tmp_path):
    root = tmp_path / "app"
    source = tmp_path / "video.mp4"
    source.write_bytes(b"0123456789")
    published = ArtifactAssets(root).publish("file", {"path": str(source)})
    app = create_app(root, token="test", origin="http://testserver", runtime=FakeRuntime())
    with TestClient(app) as client:
        assert client.get(published["src"]).status_code == 401
        client.headers["authorization"] = "Bearer test"
        response = client.post("/api/v1/agent/attachments", data={"id": "upload"},
                               files={"files": ("notes.txt", b"notes", "text/plain")})
        assert response.status_code == 202
        assert client.get("/api/v1/bucket/notes.txt").content == b"notes"
        response = client.get(published["src"], headers={"Range": "bytes=2-5"})
        assert response.status_code == 206
        assert response.content == b"2345"
        assert response.headers["content-type"] == "video/mp4"
    assert len(list((root / "bucket").iterdir())) == 2
    assert not (root / "files").exists()
    assert not (root / "artifacts").exists()


def test_bucket_serves_nested_paths_and_does_not_expose_neighboring_data(tmp_path):
    bucket = FileBucket(tmp_path)
    nested = bucket.root / "project" / "résultat #1.txt"
    nested.parent.mkdir()
    nested.write_text("result")
    secret = tmp_path / "session.json"
    secret.write_text("private")
    (bucket.root / "linked").symlink_to(secret)
    assert bucket.url(nested) == "/api/v1/bucket/project/r%C3%A9sultat%20%231.txt"
    assert asyncio.run(bucket.content("project/résultat #1.txt")).path == nested
    for path in ["../session.json", "linked", "missing", ""]:
        with pytest.raises(HttpError):
            asyncio.run(bucket.content(path))
    with pytest.raises(ValueError):
        bucket.url(secret)


def test_bucket_http_nested_paths_keep_authentication(tmp_path):
    nested = tmp_path / "bucket" / "project" / "image.png"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"test-image")
    app = create_app(tmp_path, token="test", origin="http://testserver", runtime=FakeRuntime())
    with TestClient(app) as client:
        assert client.get("/api/v1/bucket/project/image.png").status_code == 401
        client.headers["authorization"] = "Bearer test"
        assert client.get("/api/v1/bucket/project/image.png").content == b"test-image"
        assert client.get("/api/v1/bucket/").status_code == 404
