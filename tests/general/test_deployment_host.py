import asyncio
import json
from pathlib import Path

import pytest

from the_framework.deployment import Deployment, DeploymentError, HostEndpoint, HostServices
from the_framework.server.clients.browser_rpc import BrowserRPCClient

IMAGE = "sha256:" + "a" * 64


def test_private_host_boundary_auth_allowlist_and_browser_transfer(tmp_path):
    async def run():
        calls = []

        class Releases:
            def status(self):
                return {"current": IMAGE}

            def test(self, image, **kwargs):
                calls.append((image, kwargs))
                return {"status": "ok"}

            def select(self, image):
                calls.append(image)
                return image

        class Browser:
            async def observe(self, target, path, **kwargs):
                assert target == "web"
                assert Path(path).parent != tmp_path
                Path(path).write_bytes(b"frozen screenshot")
                return {"url": "about:blank"}, path

            async def open_web(self):
                return {"opened": True}

        services = HostServices(Releases(), token="control", browser_token="browser", build=lambda: IMAGE,
                                browser=Browser(), session=tmp_path / "session.json", options={"feature": True})
        address = await services.start(("127.0.0.1", 0))
        endpoint = HostEndpoint(address, "control", "browser")
        try:
            assert await asyncio.to_thread(endpoint.call, "status") == {"current": IMAGE}
            assert await asyncio.to_thread(endpoint.call, "build") == IMAGE
            assert await asyncio.to_thread(endpoint.call, "test", {"image": IMAGE}) == {"status": "ok"}
            assert calls == [(IMAGE, {"session": tmp_path / "session.json", "options": {"feature": True}})]
            assert await asyncio.to_thread(endpoint.call, "select", {"image": IMAGE}) == IMAGE
            for operation, payload in (("shell", {}), ("restart", {}), ("build", {"command": "anything"}),
                                       ("test", {"image": IMAGE, "session": "/private/session"}),
                                       ("select", {"image": "mutable:tag"})):
                with pytest.raises(DeploymentError):
                    await asyncio.to_thread(endpoint.call, operation, payload)
            wrong = HostEndpoint(address, "wrong", "also-wrong")
            with pytest.raises(DeploymentError, match="unauthorized"):
                await asyncio.to_thread(wrong.call, "status")
            # A browser credential never authorizes deployment operations.
            with pytest.raises(DeploymentError, match="unauthorized"):
                await asyncio.to_thread(endpoint.call, "status", browser=True)
            client = BrowserRPCClient(host=endpoint)
            assert await asyncio.to_thread(client.call, "open_web") == {"opened": True}
            path = tmp_path / "backend/observation.png"
            result = await asyncio.to_thread(client.call, "observe", "web", path, max_elements=40)
            assert result == ({"url": "about:blank"}, str(path))
            assert path.read_bytes() == b"frozen screenshot"
            with pytest.raises(DeploymentError, match="unsupported .* RPC operation"):
                await asyncio.to_thread(endpoint.call, "browser", {"scope": "service", "method": "stop"}, browser=True)
        finally:
            await services.close()
        assert not services.connections

    asyncio.run(run())


@pytest.mark.parametrize("address", [("0.0.0.0", 0), ("::", 0), ("8.8.8.8", 0), ("224.0.0.1", 0)])
def test_refuses_implicit_or_public_binding(tmp_path, address):
    async def run():
        services = HostServices(Deployment(tmp_path), token="control", browser_token="browser")
        with pytest.raises(ValueError, match="explicit private"):
            await services.start(address)
    asyncio.run(run())


def test_runtime_browser_uses_private_runner_settings(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"host": {"address": ["127.0.0.1", 1234], "token": "control", "browser_token": "browser"}}))
    monkeypatch.setenv("THE_FRAMEWORK_HOST_SETTINGS", str(settings))
    browser = BrowserRPCClient.for_runtime(tmp_path)
    assert browser.host.address == ("127.0.0.1", 1234)
    assert "control" not in repr(browser.host)
    settings.write_text(json.dumps({"validation": True, "host": {}}))
    with pytest.raises(RuntimeError, match="validation"):
        BrowserRPCClient.for_runtime(tmp_path)


def test_host_does_not_leak_credentials_in_failure(tmp_path):
    async def run():
        def fail():
            raise RuntimeError("failure with control-secret and browser-secret")
        services = HostServices(Deployment(tmp_path), token="control-secret", browser_token="browser-secret", build=fail)
        endpoint = HostEndpoint(await services.start(("127.0.0.1", 0)), "control-secret", "browser-secret")
        try:
            with pytest.raises(DeploymentError) as failure:
                await asyncio.to_thread(endpoint.call, "build")
            assert "control-secret" not in str(failure.value)
            assert "browser-secret" not in str(failure.value)
            assert "[redacted]" in str(failure.value)
        finally:
            await services.close()
    asyncio.run(run())
