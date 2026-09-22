"""Exercise the reusable packages from a built wheel, outside the checkout."""

from pathlib import Path
from email.parser import Parser
import shutil
import subprocess
import sys
import zipfile

import pytest

from test_framework_independence import (
    test_framework_and_general_plugins_do_not_require_product_modules as check_independence,
)


def test_wheel_contains_standalone_framework_and_prompt_resources(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("wheel verification requires uv build")
    root = Path(__file__).resolve().parents[2]
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(tmp_path / "dist"), str(root)],
        check=True, capture_output=True, text=True, timeout=120,
    )
    wheel, = (tmp_path / "dist").glob("*.whl")
    assert wheel.name.startswith("b4pt0r_the_framework-")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(wheel) as archive:
        metadata_path, = (name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_path).decode())
        assert metadata["Name"] == "b4pt0r-the-framework"
        assert metadata["License-Expression"] == "MIT"
        assert metadata["Requires-Python"] == ">=3.12"
        assert "# The Framework" in metadata.get_payload()
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in archive.namelist())
        assert not any(name.startswith(("agent/", "agent_plugins/", "server/"))
                       for name in archive.namelist())
        assert "the_framework/py.typed" in archive.namelist()
        assert "the_framework/utils/ids.py" in archive.namelist()
        assert "the_framework/utils/persistence.py" in archive.namelist()
        assert "the_framework/utils/tokens.py" in archive.namelist()
        assert "the_framework/ids.py" not in archive.namelist()
        assert "the_framework/persistence.py" not in archive.namelist()
        assert "the_framework/tokens.py" not in archive.namelist()
        for member in archive.namelist():
            if member.startswith("the_framework/"):
                archive.extract(member, installed)
    monkeypatch.chdir(installed)
    monkeypatch.setenv("PYTHONPATH", str(installed))
    monkeypatch.setenv("FRAMEWORK_PACKAGE_ROOT", str(installed))
    # The subprocess rejects product imports and constructs the real plugins,
    # including their packaged Markdown instructions and curator profiles.
    check_independence()
    subprocess.run(
        [sys.executable, "-c", (
            "from the_framework import AgentApplication, AgentSpec, ClientSurface, "
            "Extension, Plugin, PluginSpec, QueuePolicy, SessionPolicy, endpoint, "
            "provider, tool; "
            "a = AgentApplication(name='Wheel', version='1', primary_agent=AgentSpec("
            "name='main', description='Main agent.', session=SessionPolicy.durable())); "
            "s = ClientSurface(name='ui', source='.', build=('true',), artifact='dist', routes=('/ui',)); "
            "assert a.compile().primary_agent.name == 'main' and s.name == 'ui'"
        )],
        check=True, capture_output=True, text=True, timeout=30,
    )
    # Both canonical and specialist supervisors launch this module. Resolve it
    # from the wheel alone, not accidentally from an editable source checkout.
    subprocess.run(
        [sys.executable, "-m", "the_framework.agent.runtime.worker_process", "--help"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    code = tmp_path / "my-agent"
    data = tmp_path / "my-agent-data"
    subprocess.run(
        [sys.executable, "-m", "the_framework", "bootstrap", "--code-dir", str(code), "--data-dir", str(data)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert (code / "starter/ui/src/realtime-audio.js").is_file()
    assert not (code / "starter/ui/dist").exists()
    subprocess.run(
        [sys.executable, "-c", (
            "from pathlib import Path; from starter.application import application; "
            "from starter.desktop import default_data_dir; "
            "assert application.compile().primary_agent.name == 'assistant'; "
            f"assert default_data_dir() == Path({str(data)!r})"
        )],
        cwd=code, check=True, capture_output=True, text=True, timeout=30,
    )
