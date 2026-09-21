"""Keep the browser and listening socket alive across server replacement."""

import argparse
import asyncio
import json
import os
import secrets
import signal
import socket
import sys
import urllib.request
from pathlib import Path

from the_framework.server.clients.browser import BrowserService
from the_framework.server.clients.browser_rpc import (
    BrowserRPCServer,
    browser_socket_path,
)
from the_framework.server.runtime.discovery import ServerLease

from .security import LocalSecurity

RESTART = 75


async def serve(root, listener, token):
    import uvicorn

    from .server import create_app

    requested = False

    async def restart(_intent):
        nonlocal requested
        requested = True
        server.should_exit = True

    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    app = create_app(root, token=token, origin=origin, restart=restart)
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, timeout_graceful_shutdown=10))
    await server.serve(sockets=[listener])
    return RESTART if requested else 0


async def wait_ready(origin, token, process):
    def probe():
        request = urllib.request.Request(origin + "/api/v1/health", headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=1) as response:
            return response.status == 200

    async with asyncio.timeout(30):
        while process.returncode is None:
            try:
                if await asyncio.to_thread(probe):
                    return
            except OSError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("starter server exited before becoming ready")


async def desktop(root, *, port=0, no_browser=False, headless=False):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(32)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    browser = BrowserService(root, ui_headless=headless, web_headless=headless)
    rpc = BrowserRPCServer(browser_socket_path(root), browser)
    process = None
    stop_task = asyncio.create_task(stopped.wait())

    async def startup(operation):
        task = asyncio.create_task(operation)
        try:
            done, _ = await asyncio.wait((task, stop_task), return_when=asyncio.FIRST_COMPLETED)
            if stop_task in done:
                return False
            await task
            return True
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    with ServerLease(root / "desktop.lock"), socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
        listener.listen()
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        try:
            await rpc.start()
            opened = False
            while not stopped.is_set():
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "starter", "--data-dir", str(root),
                    "--server-fd", str(listener.fileno()),
                    pass_fds=(listener.fileno(),),
                    env={**os.environ, "STARTER_SESSION_TOKEN": token},
                )
                if not await startup(wait_ready(origin, token, process)):
                    break
                if not opened and not no_browser:
                    for target in ("harness_ui", "web"):
                        session = browser.session(target)
                        session.set_attr("initial_cookies", [{
                            "name": LocalSecurity.cookie, "value": token, "url": origin,
                            "httpOnly": True, "sameSite": "Strict",
                        }])
                    if not await startup(browser.open_harness_ui(origin + "/ui/")):
                        break
                    opened = True
                print(f"Pandora ready at {origin}", flush=True)
                exited = asyncio.create_task(process.wait())
                done, _ = await asyncio.wait((exited, stop_task), return_when=asyncio.FIRST_COMPLETED)
                if stop_task in done:
                    break
                if exited.result() != RESTART:
                    if exited.result():
                        raise RuntimeError(f"starter server exited with code {exited.result()}")
                    break
        finally:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 15)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            await rpc.close()
            await browser.stop()


def default_data_dir():
    instance = Path(__file__).with_name("instance.json")
    return (
        Path(json.loads(instance.read_text(encoding="utf-8"))["data_dir"])
        if instance.exists() else Path.home() / ".local/share/local-agent"
    )


def main():
    parser = argparse.ArgumentParser(description="A neutral local-agent desktop starter")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--server-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    if args.server_fd is not None:
        token = os.environ.pop("STARTER_SESSION_TOKEN")
        with socket.socket(fileno=args.server_fd) as listener:
            raise SystemExit(asyncio.run(serve(root, listener, token)))
    asyncio.run(desktop(root, port=args.port, no_browser=args.no_browser, headless=args.headless))
