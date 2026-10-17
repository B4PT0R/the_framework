"""Keep the browser and listening socket alive across server replacement."""

import argparse
import asyncio
import json
import socket
import sys
import urllib.request
from pathlib import Path

from the_framework.server.clients.browser import BrowserService
from the_framework.server.clients.browser_rpc import (
    BrowserRPCServer,
    browser_socket_path,
)
from the_framework.server.runtime.discovery import PersistentSecretStore, ServerDescriptorStore, ServerLease
from the_framework.server.runtime.local import LocalServer, RESTART_EXIT_CODE, listening_socket, shutdown_event

from .security import LocalSecurity


async def serve(root, listener):
    from .server import create_app

    with LocalServer(root / "runtime/server.json", listener=listener) as process:
        app = create_app(root, token=process.token, browser_token=process.browser_token,
                         origin=process.origin, restart=process.request_restart)
        return await process.serve(app)


async def wait_ready(store, process):
    def probe():
        descriptor = store.read()
        if descriptor["pid"] != process.pid:
            return False
        origin = f"http://{descriptor['host']}:{descriptor['port']}"
        request = urllib.request.Request(origin + "/api/v1/health", headers={"Authorization": "Bearer " + descriptor["token"]})
        with urllib.request.urlopen(request, timeout=1) as response:
            return response.status == 200 and json.load(response).get("status") == "ok"

    async with asyncio.timeout(30):
        while process.returncode is None:
            try:
                if await asyncio.to_thread(probe):
                    return
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("starter server exited before becoming ready")


async def desktop(root, *, port=0, no_browser=False, headless=False):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    browser = BrowserService(root, ui_headless=headless, web_headless=headless)
    rpc = BrowserRPCServer(browser_socket_path(root), browser)
    process = None

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

    with ServerLease(root / "desktop.lock"), listening_socket(port) as listener, shutdown_event() as stopped:
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        browser_token = PersistentSecretStore(root / "runtime/ui-session.key").load_or_create()
        async with rpc:
            stop_task = asyncio.create_task(stopped.wait())
            exited = None
            try:
                opened = False
                while not stopped.is_set():
                    process = await asyncio.create_subprocess_exec(
                        sys.executable, "-m", "starter", "--data-dir", str(root),
                        "--server-fd", str(listener.fileno()),
                        pass_fds=(listener.fileno(),),
                    )
                    if not await startup(wait_ready(ServerDescriptorStore(root / "runtime/server.json"), process)):
                        break
                    if not opened and not no_browser:
                        for target in ("harness_ui", "web"):
                            session = browser.session(target)
                            session.set_attr("initial_cookies", [{
                                "name": LocalSecurity.cookie, "value": browser_token, "url": origin,
                                "httpOnly": True, "sameSite": "Strict",
                            }])
                        if not await startup(browser.open_application_ui(origin + "/ui/")):
                            break
                        opened = True
                    print(f"Pandora ready at {origin}", flush=True)
                    exited = asyncio.create_task(process.wait())
                    done, _ = await asyncio.wait((exited, stop_task), return_when=asyncio.FIRST_COMPLETED)
                    if stop_task in done:
                        break
                    if exited.result() != RESTART_EXIT_CODE:
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
                tasks = [stop_task] + ([exited] if exited is not None else [])
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


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
        with socket.socket(fileno=args.server_fd) as listener:
            raise SystemExit(asyncio.run(serve(root, listener)))
    asyncio.run(desktop(root, port=args.port, no_browser=args.no_browser, headless=args.headless))
