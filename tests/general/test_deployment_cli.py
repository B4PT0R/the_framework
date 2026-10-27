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
    runtime = SimpleNamespace(host=SimpleNamespace(token="control", browser_token="browser", address=("127.0.0.1", 12345)), options={})
    deployment = SimpleNamespace(root=Path("unused"), serve=serve)
    with pytest.raises(RuntimeError, match="failed backend"):
        asyncio.run(cli._serve(deployment, SimpleNamespace(build=lambda: IMAGE), runtime, "127.0.0.1", None, lambda root: Browser()))
    assert calls == ["start", "serve", "close", "browser-stop"]
