"""The test runner must not borrow the live application's browser runtime."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("cache", [None, "~/explicit-browser-cache"])
def test_browser_cache_is_selected_explicitly(monkeypatch, cache):
    spec = importlib.util.spec_from_file_location("browser_test_setup", Path(__file__).parents[1] / "conftest.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/unrelated-running-application")
    setup.pytest_configure(SimpleNamespace(getoption=lambda name: cache))
    import os
    expected = str(Path(cache).expanduser().resolve()) if cache else None
    assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == expected
