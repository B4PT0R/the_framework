"""Runtime environment is active before constructing the application."""

import asyncio
import json
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from the_framework.deployment import runner


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX timezone support")
def test_runtime_timezone_applies_before_factory(monkeypatch):
    observed = []

    def factory(**kwargs):
        observed.append(datetime(2026, 10, 27, 12).astimezone().utcoffset().total_seconds())
        return object()

    class Server:
        def __init__(self, config):
            pass

        async def serve(self):
            pass

    try:
        with monkeypatch.context() as patch:
            patch.setenv("TZ", "UTC")
            time.tzset()
            patch.setattr(runner, "Path", lambda path: SimpleNamespace(
                read_text=lambda **kwargs: json.dumps({"factory": "fixture:create_app"})))
            patch.setattr(runner.importlib, "import_module", lambda name: SimpleNamespace(create_app=factory))
            patch.setitem(sys.modules, "uvicorn", SimpleNamespace(Server=Server, Config=lambda *args, **kwargs: None))
            assert asyncio.run(runner.serve({
                "environment": {"TZ": "Europe/Paris"},
                "token": "local", "browser_token": "browser", "origin": "http://localhost:8765",
            })) == 0
            assert observed == [3600]
    finally:
        time.tzset()
