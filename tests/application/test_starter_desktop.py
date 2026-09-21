"""Exercise the launch hierarchy without inference or the user's desktop."""

import asyncio
import select
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def test_starter_shutdown_cancels_pending_startup(tmp_path, monkeypatch):
    from starter import desktop

    async def check():
        handlers = {}
        started = asyncio.Event()
        cancelled = asyncio.Event()
        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler",
                            lambda sig, callback: handlers.__setitem__(sig, callback))

        async def waiting(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(desktop, "wait_ready", waiting)
        task = asyncio.create_task(desktop.desktop(tmp_path, no_browser=True))
        try:
            await asyncio.wait_for(started.wait(), 5)
            handlers[signal.SIGTERM]()
            await asyncio.wait_for(task, 10)
            assert cancelled.is_set()
            assert not (tmp_path / "runtime/browser.sock").exists()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(check())


def test_starter_launcher_starts_and_stops_isolated_server(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-m", "starter", "--data-dir", str(tmp_path), "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        origin = None
        while time.monotonic() < deadline and process.poll() is None:
            if select.select([process.stdout], [], [], 0.2)[0]:
                line = process.stdout.readline()
                if line.startswith("Pandora ready at "):
                    origin = line.strip().split(" at ", 1)[1]
                    break
        assert origin is not None, "isolated starter did not become ready"
        try:
            urllib.request.urlopen(origin + "/api/v1/status", timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("local API accepted an unauthenticated request")
        assert (tmp_path / "runtime/browser.sock").exists()
        process.terminate()
        assert process.wait(timeout=20) == 0
        assert not (tmp_path / "runtime/browser.sock").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
