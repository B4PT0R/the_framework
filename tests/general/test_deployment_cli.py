"""The opt-in command line never installs services or selects implicitly."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from the_framework.deployment import BackendImage
from the_framework.deployment import cli

IMAGE = "sha256:" + "a" * 64


def test_cli_build_combines_only_explicit_inputs(tmp_path, monkeypatch, capsys):
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").touch()
    wheel = tmp_path / "framework.whl"
    wheel.touch()
    seen = []
    monkeypatch.setattr(BackendImage, "build", lambda self: seen.append(self) or IMAGE)
    cli.main("app:create_app", projects=(project,), argv=[
        "--root", str(tmp_path / "state"), "--wheel", str(wheel), "build",
    ])
    assert seen[0].projects == (project,) and seen[0].wheels == (wheel,)
    assert capsys.readouterr().out.strip() == IMAGE
    assert not (tmp_path / "state").exists()


def test_status_requires_no_build_inputs_or_runtime(tmp_path, capsys):
    cli.main("app:create_app", argv=["--root", str(tmp_path), "status"])
    assert json.loads(capsys.readouterr().out)["selected"] is None


def test_cli_test_only_forwards_nonsecret_options(tmp_path, monkeypatch):
    settings = tmp_path / "private.json"
    settings.write_text(json.dumps({"token": "never-used", "environment": {"SECRET": "unused"}, "options": {"enabled": True}}))
    calls = []
    monkeypatch.setattr(cli.Deployment, "test", lambda self, *args, **kwargs: calls.append((args, kwargs)))
    cli.main("app:create_app", argv=["--root", str(tmp_path), "--runtime", str(settings), "test", IMAGE])
    assert calls == [((IMAGE,), {"session": None, "options": {"enabled": True}})]


def test_cli_requires_explicit_inputs_before_build(tmp_path):
    with pytest.raises(SystemExit):
        cli.main("app:create_app", argv=["--root", str(tmp_path), "build"])


def test_serve_failure_stops_and_closes_host_resources(monkeypatch):
    calls = []

    class Services:
        def __init__(self, *args, **kwargs):
            pass

        async def start(self, address):
            calls.append("start")

        async def close(self):
            calls.append("close")

    class Browser:
        async def stop(self):
            calls.append("browser-stop")

    def serve(runtime, *, stop):
        calls.append("serve")
        raise RuntimeError("failed backend")

    monkeypatch.setattr(cli, "HostServices", Services)
    runtime = SimpleNamespace(host=SimpleNamespace(token="control", browser_token="browser", execution_token=None, address=("127.0.0.1", 12345)), options={})
    deployment = SimpleNamespace(root=Path("unused"), serve=serve)
    with pytest.raises(RuntimeError, match="failed backend"):
        asyncio.run(cli._serve(deployment, SimpleNamespace(build=lambda: IMAGE), runtime, "127.0.0.1", None, lambda root: Browser()))
    assert calls == ["start", "serve", "close", "browser-stop"]


@pytest.mark.parametrize("credential", [None, "execution-secret"])
@pytest.mark.parametrize("failure", [None, "construct", "start", "serve", "cancel"])
def test_execution_factory_is_opt_in_and_resources_are_owned(monkeypatch, tmp_path, credential, failure):
    from the_framework.deployment import BackendRuntime, HostEndpoint

    calls = []
    execution = SimpleNamespace(close=None)

    async def close_execution():
        calls.append("execution-close")

    execution.close = close_execution

    def factory():
        calls.append("execution-create")
        return execution

    class Services:
        def __init__(self, *args, **kwargs):
            assert kwargs["execution"] is (execution if credential else None)
            assert kwargs["execution_token"] == credential
            calls.append("construct")
            if failure == "construct":
                raise RuntimeError("construct")

        async def start(self, address):
            calls.append("start")
            if failure == "start":
                raise RuntimeError("start")
            if failure == "cancel":
                raise asyncio.CancelledError()

        async def close(self):
            calls.append("close")
            if credential:
                await execution.close()

    def serve(runtime, *, stop):
        calls.append("serve")
        if failure == "serve":
            raise RuntimeError("serve")

    monkeypatch.setattr(cli, "HostServices", Services)
    runtime = BackendRuntime(token="backend", browser_token="backend-browser", host=HostEndpoint(
        ("127.0.0.1", 12345), "control", "browser", execution_token=credential))
    deployment = SimpleNamespace(root=tmp_path, serve=serve)
    async def run():
        await cli._serve(deployment, SimpleNamespace(build=lambda: IMAGE), runtime,
                         "127.0.0.1", None, None, factory)
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else RuntimeError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert calls.count("execution-create") == bool(credential)
    assert calls.count("execution-close") == bool(credential)
    assert calls.count("close") == (failure != "construct")
    assert ("serve" in calls) == (failure in (None, "serve"))


def test_execution_credential_without_factory_refuses_before_allocation(tmp_path):
    from the_framework.deployment import BackendRuntime, HostEndpoint

    runtime = BackendRuntime(token="backend", browser_token="backend-browser", host=HostEndpoint(
        ("127.0.0.1", 12345), "control", "browser", execution_token="execution"))
    def unexpected(*args, **kwargs):
        pytest.fail("must reject before creating browser or backend")
    with pytest.raises(ValueError, match="explicit execution_factory"):
        asyncio.run(cli._serve(SimpleNamespace(root=tmp_path, serve=unexpected), None, runtime,
                               "127.0.0.1", None, unexpected))


def test_without_host_neither_factory_is_called(tmp_path):
    def unexpected(*args):
        pytest.fail("host factory called without a host endpoint")

    calls = []
    deployment = SimpleNamespace(root=tmp_path, serve=lambda runtime, **kwargs: calls.append(runtime))
    runtime = SimpleNamespace(host=None)
    asyncio.run(cli._serve(deployment, None, runtime, None, None, unexpected, unexpected))
    assert calls == [runtime]


def test_cancellation_drains_backend_before_closing_execution(monkeypatch, tmp_path):
    from threading import Event

    calls = []
    started = Event()

    class Services:
        def __init__(self, *args, **kwargs):
            self.execution = kwargs["execution"]

        async def start(self, address):
            pass

        async def close(self):
            calls.append("services-close")
            await self.execution.close()

    async def close_execution():
        calls.append("execution-close")

    def serve(runtime, *, stop):
        started.set()
        assert stop.wait(5), "backend was not signalled to stop"
        calls.append("backend-drained")

    monkeypatch.setattr(cli, "HostServices", Services)
    runtime = SimpleNamespace(host=SimpleNamespace(
        token="control", browser_token="browser", execution_token="execution",
        address=("127.0.0.1", 12345)), options={})
    deployment = SimpleNamespace(root=tmp_path, serve=serve)

    async def run():
        task = asyncio.create_task(cli._serve(
            deployment, SimpleNamespace(build=None), runtime, "127.0.0.1", None,
            None, lambda: SimpleNamespace(close=close_execution)))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert calls == ["backend-drained", "services-close", "execution-close"]
