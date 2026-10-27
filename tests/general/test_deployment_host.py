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


def test_execution_stream_credentials_status_and_shutdown(tmp_path):
    import sys
    from the_framework.execution import ProcessHost, RemoteProcess

    def recipe(fd):
        return [sys.executable, "-c", (
            "import socket,sys; s=socket.socket(fileno=int(sys.argv[1])); "
            "s.sendall(b'ready'); data=s.recv(100); s.sendall(data[::-1])"
        ), str(fd)]

    async def run():
        processes = ProcessHost({"echo": recipe})
        services = HostServices(Deployment(tmp_path), token="control", browser_token="browser",
                                execution=processes, execution_token="execute")
        address = await services.start(("127.0.0.1", 0))
        endpoint = HostEndpoint(address, "control", "browser", "execute")
        process = None
        try:
            for credential in ("control", "browser", "wrong"):
                bad = HostEndpoint(address, "unused-control", "unused-browser", credential)
                with pytest.raises(DeploymentError, match="unauthorized"):
                    await asyncio.to_thread(bad.open_process, "echo")
            with pytest.raises(DeploymentError, match="unauthorized"):
                await asyncio.to_thread(endpoint._call, "status", {}, "execute", 5)
            with pytest.raises(DeploymentError, match="unknown .* recipe"):
                await asyncio.to_thread(endpoint.open_process, "arbitrary.module")
            process = await asyncio.to_thread(RemoteProcess, endpoint, "echo")
            # The ready bytes may share a TCP packet with the host handshake.
            assert await asyncio.to_thread(process.socket.recv, 5) == b"ready"
            await asyncio.to_thread(process.socket.sendall, b"abc")
            assert await asyncio.to_thread(process.socket.recv, 3) == b"cba"
            assert await asyncio.to_thread(process.wait, 5) == 0
            await asyncio.to_thread(process.close)
            assert process.poll() == 0
            idle = await asyncio.to_thread(RemoteProcess, endpoint, "echo")
            assert await asyncio.to_thread(idle.socket.recv, 5) == b"ready"
            await asyncio.wait_for(services.close(), 5)
            assert await asyncio.to_thread(idle.socket.recv, 1) == b""
            idle.socket.close()
            assert not processes.processes
            assert not services.connections
        finally:
            if process is not None:
                await asyncio.to_thread(process.close)
            await services.close()
    asyncio.run(run())


def test_execution_opt_in_and_credential_separation(tmp_path):
    from the_framework.execution import ProcessHost
    for credential in ("", "control", "browser"):
        with pytest.raises(ValueError, match="distinct"):
            HostEndpoint(("127.0.0.1", 1234), "control", "browser", credential)
        with pytest.raises(ValueError, match="distinct"):
            HostServices(Deployment(tmp_path), token="control", browser_token="browser",
                         execution=ProcessHost({}), execution_token=credential)
    with pytest.raises(ValueError, match="together"):
        HostServices(Deployment(tmp_path), token="control", browser_token="browser",
                     execution=ProcessHost({}))
    endpoint = HostEndpoint(("127.0.0.1", 1234), "control", "browser")
    with pytest.raises(DeploymentError, match="not configured"):
        endpoint.open_process("echo")


def test_backend_credentials_cannot_authorize_host_execution():
    from the_framework.deployment import BackendRuntime
    for credential in ("backend", "backend-browser"):
        endpoint = HostEndpoint(("127.0.0.1", 1234), "control", "browser", credential)
        with pytest.raises(ValueError, match="distinct from backend"):
            BackendRuntime(token="backend", browser_token="backend-browser", host=endpoint)
