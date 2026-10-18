import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image as Raster, UnidentifiedImageError

from the_framework.agent import Agent, Image
from the_framework.agent.context.session import Session
from the_framework.utils import images


def png(format="PNG"):
    output = BytesIO()
    Raster.new("RGB", (12, 8), "navy").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def remote(monkeypatch):
    """Run the real HTTP client/redirect code against a controlled transport."""
    requests = []
    responses = {}

    def respond(request):
        requests.append(str(request.url))
        result = responses[str(request.url)]
        if isinstance(result, Exception):
            raise result
        return result

    original = httpx.stream
    with httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        monkeypatch.setattr(images.httpx, "stream", client.stream)
        yield requests, responses
    monkeypatch.setattr(images.httpx, "stream", original)


def test_remote_image_is_frozen_before_history_and_projected_as_base64(tmp_path, remote):
    requests, responses = remote
    source = "https://example.test/signed-image?token=private"
    data = png()
    responses[source] = httpx.Response(302, headers={"location": "/image"})
    # Neither suffix nor Content-Type is trusted over the actual image bytes.
    responses["https://example.test/image"] = httpx.Response(200, content=data, headers={"content-type": "application/octet-stream"})
    image = Image.from_source(source, directory=tmp_path / "images", description="Remote reference")
    assert Path(image.path).suffix == ".png"
    assert Path(image.path).read_bytes() == data
    assert len(requests) == 2
    session = Session.open(tmp_path / "session.json")
    assert session.append(image) is image
    restored = Session.open(session.path).history[0]
    # No re-download during replay/projection, even if the URL stops working.
    responses.clear()
    payload = restored.to_api_format()
    api_image = payload["content"][0]["image_url"]
    assert api_image == "data:image/png;base64," + base64.b64encode(data).decode()
    assert source not in json.dumps(payload)
    assert source not in session.path.read_text()
    assert base64.b64encode(data).decode() not in session.path.read_text()
    assert len(requests) == 2
    agent = Agent(client=object(), session=session)
    assert agent.context.input()[0]["content"][0]["image_url"] == api_image


@pytest.mark.parametrize("source_kind", ["bytes", "local"])
@pytest.mark.parametrize("format, suffix", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp"), ("GIF", ".gif")])
def test_local_and_binary_sources_are_validated_and_immutable(tmp_path, source_kind, format, suffix):
    data = png(format)
    source = tmp_path / "original"
    source.write_bytes(data)
    image = Image.from_source(data if source_kind == "bytes" else source, directory=tmp_path / "images", description="Reference")
    source.unlink()
    assert Path(image.path).suffix == suffix
    assert Path(image.path).read_bytes() == data
    assert Image.from_source(data, directory=tmp_path / "images").path == image.path


@pytest.mark.parametrize("result, error", [
    (httpx.Response(403), httpx.HTTPStatusError),
    (httpx.Response(404), httpx.HTTPStatusError),
    (httpx.Response(200, content=b"<html>Login required</html>"), UnidentifiedImageError),
    (httpx.Response(200, content=b""), UnidentifiedImageError),
    (httpx.Response(200, content=png()[:30]), OSError),
    (httpx.ReadTimeout("Timed out"), httpx.ReadTimeout),
    (httpx.ConnectError("Connection failed"), httpx.ConnectError),
])
def test_bad_remote_sources_fail_before_session_injection(tmp_path, remote, result, error):
    requests, responses = remote
    responses["https://example.test/image"] = result
    agent = Agent(client=object(), session_path=tmp_path / "session.json")
    with pytest.raises(error):
        agent.add_response_item(Image.from_source("https://example.test/image", directory=tmp_path / "images"))
    assert not agent.session.history
    assert not (tmp_path / "images").exists()
    assert len(requests) == 1


def test_redirect_loop_fails_locally(tmp_path, remote):
    requests, responses = remote
    responses["https://example.test/loop"] = httpx.Response(302, headers={"location": "/loop"})
    with pytest.raises(httpx.TooManyRedirects):
        Image.from_source("https://example.test/loop", directory=tmp_path / "images")
    assert not (tmp_path / "images").exists()


@pytest.mark.parametrize("source_kind", ["bytes", "local", "url"])
def test_oversized_images_are_rejected(tmp_path, monkeypatch, remote, source_kind):
    requests, responses = remote
    data = png()
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", len(data) - 1)
    source = tmp_path / "original.png"
    source.write_bytes(data)
    responses["https://example.test/image"] = httpx.Response(200, content=data)
    source = {"bytes": data, "local": source, "url": "https://example.test/image"}[source_kind]
    with pytest.raises(ValueError, match="limit"):
        Image.from_source(source, directory=tmp_path / "images")
    assert not (tmp_path / "images").exists()


@pytest.mark.parametrize("source", ["ftp://example.test/image", "file:///tmp/image.png", "data:image/png;base64,abc"])
def test_non_http_urls_are_rejected(tmp_path, source):
    with pytest.raises(ValueError, match="HTTP"):
        Image.from_source(source, directory=tmp_path / "images")


def test_concurrent_identical_sources_share_one_complete_file(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: Image.from_source(png(), directory=tmp_path), range(12)))
    assert len({image.path for image in results}) == 1
    assert Path(results[0].path).read_bytes() == png()
    assert len(list(tmp_path.iterdir())) == 1
