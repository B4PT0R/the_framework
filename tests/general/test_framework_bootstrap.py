"""A generated application is independent of the product checkout."""

import json
from importlib.metadata import version
import subprocess
import sys
import tomllib

import pytest

from the_framework.cli import bootstrap, main, prepare, run_application


def test_bootstrap_copies_editable_starter_and_separates_private_data(tmp_path):
    code = tmp_path / "code"
    data = tmp_path / "private-data"
    bootstrap(code, data)
    assert (code / "starter/application.py").is_file()
    assert (code / "starter/ui/src/realtime-audio.js").is_file()
    assert (code / "starter/ui/package-lock.json").is_file()
    assert not (code / "starter/ui/node_modules").exists()
    assert not (code / "starter/ui/dist").exists()
    assert json.loads((code / "starter/instance.json").read_text())["data_dir"] == str(data)
    assert data.stat().st_mode & 0o777 == 0o700
    manifest = tomllib.loads((code / "pyproject.toml").read_text())
    assert manifest["project"]["dependencies"] == [
        f"b4pt0r-the-framework=={version('b4pt0r-the-framework')}"
    ]
    assert not any(data.iterdir())
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path; from starter.application import application; "
            "from starter.desktop import default_data_dir; "
            "assert application.compile().agents['assistant'].name == 'assistant'; "
            f"assert default_data_dir() == Path({str(data)!r})"
        )],
        cwd=code, check=True, capture_output=True, text=True, timeout=30,
    )


def test_bootstrap_prompts_and_never_overwrites(tmp_path, monkeypatch):
    code = tmp_path / "code"
    data = tmp_path / "data"
    answers = iter((str(code), str(data)))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main(["bootstrap", "--no-prepare"]) == 0
    with pytest.raises(ValueError, match="absent or empty"):
        bootstrap(code, tmp_path / "other-data")
    with pytest.raises(ValueError, match="separate"):
        bootstrap(tmp_path / "another-code", tmp_path / "another-code/data")


@pytest.fixture
def prepared_commands(tmp_path, monkeypatch):
    from the_framework import cli
    code = tmp_path / "app with spaces"
    bootstrap(code, tmp_path / "private data")
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}")
    calls = []
    fail = {"build": False, "python": True, "browser": True}

    def run(command, *, cwd, env, check):
        assert cwd == code
        assert env["UV_PROJECT_ENVIRONMENT"] == str(code / ".venv")
        assert "VIRTUAL_ENV" not in env and "PYTHONPATH" not in env
        calls.append(command)
        ui = code / "starter/ui"
        if command == ["uv", "sync", "--check"]:
            return subprocess.CompletedProcess(command, int(fail["python"]))
        if command == ["uv", "sync"]:
            fail["python"] = False
        if command[0] == str(code / ".venv/bin/python"):
            return subprocess.CompletedProcess(command, int(fail["browser"]))
        if command[-2:] == ["install", "chromium"]:
            fail["browser"] = False
        if command[-1] == "ci":
            (ui / "node_modules").mkdir(exist_ok=True)
        if command[-2:] == ["run", "build"]:
            if fail["build"]:
                raise subprocess.CalledProcessError(1, command)
            (ui / "dist").mkdir(exist_ok=True)
            (ui / "dist/index.html").write_text("built")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", run)
    return code, calls, fail


def test_prepare_resumes_without_reinstalling_and_rebuilds_changed_ui(prepared_commands):
    code, calls, fail = prepared_commands
    fail["build"] = True
    with pytest.raises(subprocess.CalledProcessError):
        prepare(code)
    assert (code / ".framework/prepare.json").is_file()
    fail["build"] = False
    prepare(code)
    assert sum(command[-1] == "ci" for command in calls) == 1
    before = len(calls)
    prepare(code)
    assert not any(command[0] == "npm" for command in calls[before:])
    main = code / "starter/ui/src/main.jsx"
    main.write_text(main.read_text() + "\n// Application edit\n")
    prepare(code)
    assert calls[-1][-2:] == ["run", "build"]
    assert sum(command[-1] == "ci" for command in calls) == 1
    lock = code / "starter/ui/package-lock.json"
    lock.write_text(lock.read_text() + "\n")
    prepare(code)
    assert sum(command[-1] == "ci" for command in calls) == 2


def test_run_prepares_and_launches_in_application_environment(prepared_commands):
    code, calls, _ = prepared_commands
    assert run_application(code, no_browser=True, headless=True, port=48899) == 0
    assert calls[-1] == ["uv", "run", "--no-sync", "python", "-m", "starter",
                         "--port", "48899", "--no-browser", "--headless"]
    assert (code / "starter/ui/dist/index.html").is_file()


def test_missing_tools_are_diagnosed_before_creating_the_app(tmp_path, monkeypatch, capsys):
    from the_framework import cli
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    code, data = tmp_path / "code", tmp_path / "data"
    with pytest.raises(SystemExit):
        main(["bootstrap", "--code-dir", str(code), "--data-dir", str(data)])
    assert not code.exists() and not data.exists()
    assert "Missing prerequisites: uv, node, npm" in capsys.readouterr().err


def test_cli_default_bootstrap_prepares_and_prints_one_run_command(tmp_path, monkeypatch, capsys):
    from the_framework import cli
    monkeypatch.setattr(cli.shutil, "which", lambda name: name)
    prepared = []
    monkeypatch.setattr(cli, "prepare", lambda code: prepared.append(code))
    code, data = tmp_path / "app", tmp_path / "data"
    assert main(["bootstrap", "--code-dir", str(code), "--data-dir", str(data)]) == 0
    assert prepared == [code]
    assert "the-framework run --code-dir" in capsys.readouterr().out


def test_prepare_cli_reports_a_resumable_failed_step(prepared_commands, capsys):
    code, _, fail = prepared_commands
    fail["build"] = True
    assert main(["prepare", "--code-dir", str(code)]) == 1
    assert "Resume with:" in capsys.readouterr().err
    assert (code / "starter/application.py").is_file()


def test_prepared_run_checks_real_resources_without_reinstalling(prepared_commands):
    code, calls, fail = prepared_commands
    prepare(code)
    calls.clear()
    assert run_application(code) == 0
    assert ["uv", "sync"] not in calls
    assert not any(command[-2:] == ["install", "chromium"] for command in calls)
    assert not any(command[0] == "npm" for command in calls)


@pytest.mark.parametrize("missing", ["python", "browser"])
def test_run_repairs_missing_resources_despite_previous_preparation(prepared_commands, missing):
    code, calls, fail = prepared_commands
    prepare(code)
    calls.clear()
    fail[missing] = True
    assert run_application(code) == 0
    assert not fail[missing]
    assert (["uv", "sync"] in calls) == (missing == "python")
    assert any(command[-2:] == ["install", "chromium"] for command in calls) == (missing == "browser")
    assert not any(command[0] == "npm" for command in calls)


def test_explicit_prepare_refreshes_python_and_browser(prepared_commands):
    code, calls, fail = prepared_commands
    prepare(code)
    calls.clear()
    assert main(["prepare", "--code-dir", str(code)]) == 0
    assert ["uv", "sync"] in calls
    assert any(command[-2:] == ["install", "chromium"] for command in calls)
