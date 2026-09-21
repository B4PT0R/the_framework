"""A generated application is independent of the product checkout."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core.cli import bootstrap, main


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
    assert not any(data.iterdir())
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path; from starter.application import application; "
            "from starter.desktop import default_data_dir; "
            "assert application.compile().primary_agent.name == 'assistant'; "
            f"assert default_data_dir() == Path({str(data)!r})"
        )],
        cwd=code, check=True, capture_output=True, text=True, timeout=30,
    )


def test_bootstrap_prompts_and_never_overwrites(tmp_path, monkeypatch):
    code = tmp_path / "code"
    data = tmp_path / "data"
    answers = iter((str(code), str(data)))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main(["bootstrap"]) == 0
    with pytest.raises(ValueError, match="absent or empty"):
        bootstrap(code, tmp_path / "other-data")
    with pytest.raises(ValueError, match="separate"):
        bootstrap(tmp_path / "another-code", tmp_path / "another-code/data")
