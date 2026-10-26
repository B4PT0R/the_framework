"""Bounded multipart ingress and transactional, non-overwriting file copies."""

import asyncio
import io
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette import formparsers

from the_framework import Application, BuildContext
from the_framework.server.api.chat import ChatApi
from the_framework.server.api.endpoints import EndpointRegistry, PermitAllSecurity
from the_framework.server.api.uploads import copy_uploaded_files
from tests.fixtures.runtime import FakeRuntime


def upload(name, data):
    return UploadFile(io.BytesIO(data), filename=name)


def chat_app(root, limit):
    runtime = FakeRuntime()
    app = FastAPI()
    registry = EndpointRegistry(
        app, security=PermitAllSecurity(), application=SimpleNamespace(max_upload_bytes=limit),
    )
    registry.add_source(ChatApi(runtime, root, max_upload_bytes=limit))
    registry.install()
    return app, runtime


def track_spools(monkeypatch):
    opened = []
    original = formparsers.SpooledTemporaryFile

    def create(*args, **kwargs):
        result = original(*args, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", create)
    return opened


@pytest.mark.parametrize("parts", [[b"123456789"], [b"1234", b"56789"]])
def test_copy_limit_is_aggregate_and_cleans_temporaries(tmp_path, parts):
    existing = tmp_path / "keep.txt"
    existing.write_bytes(b"existing")
    with pytest.raises(ValueError, match="combined upload limit"):
        copy_uploaded_files(
            [upload(f"{index}.txt", part) for index, part in enumerate(parts)],
            tmp_path, max_total_bytes=8,
        )
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"existing"


def test_copy_exact_limit_and_duplicate_names_preserve_existing(tmp_path):
    (tmp_path / "keep.txt").write_bytes(b"existing")
    copied = copy_uploaded_files(
        [upload("../keep.txt", b"1234"), upload("keep.txt", b"5678")],
        tmp_path, max_total_bytes=8,
    )
    assert [item.name for item in copied] == ["keep (2).txt", "keep (3).txt"]
    assert [item.size for item in copied] == [4, 4]
    assert (tmp_path / "keep.txt").read_bytes() == b"existing"
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "keep (2).txt", "keep (3).txt", "keep.txt",
    ]


def test_copy_publish_race_does_not_overwrite_and_rolls_back(tmp_path, monkeypatch):
    from the_framework.server.api import uploads

    original = uploads.os.link

    def concurrent_link(source, destination):
        if destination.name == "second.txt":
            destination.write_bytes(b"concurrent upload")
        original(source, destination)

    monkeypatch.setattr(uploads.os, "link", concurrent_link)
    with pytest.raises(FileExistsError):
        copy_uploaded_files(
            [upload("first.txt", b"one"), upload("second.txt", b"two")], tmp_path,
        )
    assert [path.name for path in tmp_path.iterdir()] == ["second.txt"]
    assert (tmp_path / "second.txt").read_bytes() == b"concurrent upload"


@pytest.mark.parametrize("parts, status", [([b"12345678"], 202), ([b"1234", b"56789"], 422)])
def test_attachment_copy_limit_and_form_cleanup(tmp_path, monkeypatch, parts, status):
    opened = track_spools(monkeypatch)
    app, runtime = chat_app(tmp_path, 8)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/attachments", data={"id": "copy"},
            files=[("files", (f"{index}.txt", part)) for index, part in enumerate(parts)],
        )
    assert response.status_code == status
    assert len(runtime.submitted) == (status == 202)
    assert len(list(tmp_path.iterdir())) == (status == 202)
    assert opened and all(file.closed for file in opened)


@pytest.mark.parametrize("length_header", [None, "1", "999999999"])
def test_stream_limit_ignores_content_length_and_closes_partial_spool(
    tmp_path, monkeypatch, length_header,
):
    from the_framework.server.api import uploads

    monkeypatch.setattr(uploads, "MULTIPART_OVERHEAD_BYTES", 256)
    opened = track_spools(monkeypatch)
    app, runtime = chat_app(tmp_path, 8)
    sent = []

    async def body():
        chunks = [
            b'--test\r\nContent-Disposition: form-data; name="files"; filename="one.txt"\r\n\r\n',
            b"x" * 128, b"y" * 128, b"never read\r\n--test--\r\n",
        ]
        for chunk in chunks:
            sent.append(chunk)
            yield chunk

    async def run():
        headers = {"content-type": "multipart/form-data; boundary=test"}
        if length_header is not None:
            headers["content-length"] = length_header
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/api/v1/agent/attachments", headers=headers, content=body())

    response = asyncio.run(run())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    assert len(sent) == 3  # Stop reading at the crossing chunk; never feed it to the parser.
    assert opened and all(file.closed for file in opened)
    assert not runtime.submitted
    assert not list(tmp_path.iterdir())


def test_form_validation_error_also_closes_uploads(tmp_path, monkeypatch):
    opened = track_spools(monkeypatch)
    app, runtime = chat_app(tmp_path, 8)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/attachments", files={"files": ("one.txt", b"one")},
        )
    assert response.status_code == 422  # Missing required command id.
    assert opened and all(file.closed for file in opened)
    assert not runtime.submitted
    assert not list(tmp_path.iterdir())


def test_application_limit_reaches_composed_chat_service(tmp_path):
    from starter.application import application as starter
    from starter.security import LocalSecurity

    application = Application({**starter, "max_upload_bytes": 8})
    runtime = FakeRuntime()
    security = LocalSecurity("test", origin="http://testserver")
    app = application.build(
        "starter.application:application", BuildContext(data_root=tmp_path, origin="http://testserver"),
        security=security, handshake=security.handshake,
        session_factory=lambda _supervisor, _context: runtime,
    )
    with TestClient(app, headers={"authorization": "Bearer test"}) as client:
        response = client.post(
            "/api/v1/agent/attachments", data={"id": "configured"},
            files={"files": ("one.txt", b"123456789")},
        )
    assert response.status_code == 422
    assert not runtime.submitted
    assert not list((tmp_path / "bucket").iterdir())


@pytest.mark.parametrize("limit", [0, -1])
def test_application_rejects_nonpositive_upload_limit(limit):
    with pytest.raises(ValueError, match="max_upload_bytes"):
        Application(name="test", version="0", max_upload_bytes=limit)


@pytest.mark.parametrize("extra, status", [(0, 202), (-1, 413)])
def test_wire_limit_exact_boundary_includes_framing(tmp_path, monkeypatch, extra, status):
    from the_framework.server.api import uploads

    monkeypatch.setattr(uploads, "MULTIPART_OVERHEAD_BYTES", 0)
    body = (
        b'--test\r\nContent-Disposition: form-data; name="id"\r\n\r\nexact\r\n'
        b'--test\r\nContent-Disposition: form-data; name="files"; filename="one.txt"\r\n\r\n'
        b'one\r\n--test--\r\n'
    )
    app, runtime = chat_app(tmp_path, len(body) + extra)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/attachments", content=body,
            headers={"content-type": "multipart/form-data; boundary=test"},
        )
    assert response.status_code == status
    assert len(runtime.submitted) == (status == 202)


def test_explicit_unlimited_uploads_and_file_count_limit(tmp_path, monkeypatch):
    opened = track_spools(monkeypatch)
    app, runtime = chat_app(tmp_path, None)
    assert Application(name="test", version="0", max_upload_bytes=None).max_upload_bytes is None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/attachments", data={"id": "too-many"},
            files=[("files", (f"{index}.txt", b"x")) for index in range(33)],
        )
        assert response.status_code == 400
        assert opened and all(file.closed for file in opened)
        assert not runtime.submitted
        response = client.post(
            "/api/v1/agent/attachments", data={"id": "unlimited"},
            files={"files": ("one.txt", b"a" * 1024)},
        )
        assert response.status_code == 202
    assert len(runtime.submitted) == 1
    assert all(file.closed for file in opened)
    assert (tmp_path / "one.txt").read_bytes() == b"a" * 1024


@pytest.mark.parametrize("encoding", ["urlencoded", "missing"])
def test_non_multipart_forms_keep_request_validation_contract(tmp_path, encoding):
    app, runtime = chat_app(tmp_path, 8)
    with TestClient(app) as client:
        kwargs = {"data": {"id": "invalid", "files": "not a file"}} if encoding == "urlencoded" else {}
        response = client.post("/api/v1/agent/attachments", **kwargs)
    assert response.status_code == 422
    assert not runtime.submitted
    assert not list(tmp_path.iterdir())
