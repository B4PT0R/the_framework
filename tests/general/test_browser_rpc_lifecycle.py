"""Browser process shutdown owns accepted RPCs, independently of Chromium."""

import asyncio
import json
import socket

import pytest

from the_framework.server.clients.browser import BrowserError
from the_framework.server.clients.browser_rpc import BrowserRPCClient, BrowserRPCServer
from the_framework.server.runtime.discovery import ServerAlreadyRunning


@pytest.mark.parametrize("request_started", [False, True])
def test_shutdown_closes_idle_and_running_connections(tmp_path, request_started):
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Service:
            async def open_web(self):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            async def stop(self):
                if request_started:
                    assert cancelled.is_set()

        path = tmp_path / "browser.sock"
        server = await BrowserRPCServer(path, Service()).start()
        reader, writer = await asyncio.open_unix_connection(path)
        try:
            if request_started:
                writer.write(json.dumps({
                    "id": "test", "scope": "service", "method": "open_web",
                }).encode() + b"\n")
                await writer.drain()
                await asyncio.wait_for(started.wait(), 1)
            else:
                # Ensure the accepted connection belongs to the server before close.
                while not server.connections:
                    await asyncio.sleep(0)
            await asyncio.wait_for(server.close(), 1)
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert not server.connections
            assert not path.exists()
        finally:
            writer.close()
            await writer.wait_closed()

    asyncio.run(run())


def test_browser_socket_has_one_owner_and_can_be_reacquired(tmp_path):
    async def run():
        class Service:
            async def stop(self):
                pass

        path = tmp_path / "browser.sock"
        first = await BrowserRPCServer(path, Service()).start()
        second = BrowserRPCServer(path, Service())
        try:
            inode = path.stat().st_ino
            with pytest.raises(ServerAlreadyRunning):
                await second.start()
            assert path.stat().st_ino == inode
        finally:
            await first.close()
        await second.start()
        await second.close()
        # Closing the old owner again must not unlink a new owner's socket.
        third = await BrowserRPCServer(path, Service()).start()
        await first.close()
        assert path.exists()
        await third.close()

    asyncio.run(run())


def test_browser_start_preserves_regular_files_and_recovers_stale_socket(tmp_path):
    async def run():
        class Service:
            async def stop(self):
                pass

        path = tmp_path / "browser.sock"
        path.write_text("not a socket")
        server = BrowserRPCServer(path, Service())
        with pytest.raises(BrowserError, match="not a socket"):
            await server.start()
        assert path.read_text() == "not a socket"
        path.unlink()
        with socket.socket(socket.AF_UNIX) as stale:
            stale.bind(str(path))
        await server.start()
        await server.close()

    asyncio.run(run())


def test_browser_start_preserves_live_socket_without_advisory_lock(tmp_path):
    async def run():
        class Service:
            async def stop(self):
                pass

        path = tmp_path / "browser.sock"
        legacy = await asyncio.start_unix_server(lambda reader, writer: writer.close(), path)
        inode = path.stat().st_ino
        candidate = BrowserRPCServer(path, Service())
        try:
            with pytest.raises(BrowserError, match="already active"):
                await candidate.start()
            await candidate.close()
            assert path.stat().st_ino == inode
        finally:
            legacy.close()
            await legacy.wait_closed()

    asyncio.run(run())


def test_shutdown_removes_socket_even_when_browser_stop_fails(tmp_path):
    async def run():
        class Service:
            async def stop(self):
                raise RuntimeError("browser stop failed")

        path = tmp_path / "browser.sock"
        server = await BrowserRPCServer(path, Service()).start()
        with pytest.raises(RuntimeError, match="browser stop failed"):
            await server.close()
        assert server.server is None
        assert not path.exists()

    asyncio.run(run())


@pytest.mark.parametrize("method", ["open_harness_ui", "open_application_ui"])
def test_browser_accepts_ui_commands_from_workers_across_upgrade(tmp_path, method):
    async def run():
        class Service:
            async def open_application_ui(self, url):
                return {"url": url}

            async def stop(self):
                pass

        path = tmp_path / "browser.sock"
        server = await BrowserRPCServer(path, Service()).start()
        try:
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(json.dumps({
                "id": "ui", "scope": "service", "method": method,
                "args": ["http://localhost/dashboard/"],
            }).encode() + b"\n")
            await writer.drain()
            response = json.loads(await asyncio.wait_for(reader.readline(), 1))
            assert response == {
                "id": "ui", "ok": True,
                "result": {"url": "http://localhost/dashboard/"},
            }
            writer.close()
            await writer.wait_closed()
        finally:
            await server.close()

    asyncio.run(run())


def test_new_worker_can_open_ui_in_browser_started_before_upgrade(tmp_path):
    async def run():
        received = []

        async def legacy_browser(reader, writer):
            request = json.loads(await reader.readline())
            received.append(request)
            writer.write(json.dumps({
                "id": request["id"], "ok": True, "result": "opened",
            }).encode() + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        path = tmp_path / "browser.sock"
        server = await asyncio.start_unix_server(legacy_browser, path)
        try:
            client = BrowserRPCClient(path)
            result = await asyncio.to_thread(
                client.call, "open_application_ui", "http://localhost/dashboard/",
            )
            assert result == "opened"
            assert received[0]["method"] == "open_harness_ui"
            assert received[0]["args"] == ["http://localhost/dashboard/"]
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_client_disconnect_cancels_its_pending_operation_and_keeps_service_available(tmp_path):
    async def run():
        started, cancelled = asyncio.Event(), asyncio.Event()
        class Service:
            async def close_web_tab(self):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            async def open_web(self):
                return {"ready": True}
            async def stop(self):
                pass
        path = tmp_path / 'browser.sock'
        async with BrowserRPCServer(path, Service()) as server:
            _, writer = await asyncio.open_unix_connection(path)
            writer.write(json.dumps({'id': 'close', 'scope': 'service', 'method': 'close_web_tab'}).encode()+b'\n')
            await writer.drain()
            await asyncio.wait_for(started.wait(), 1)
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(cancelled.wait(), 1)
            client = BrowserRPCClient(path)
            assert await asyncio.to_thread(client.call, 'open_web') == {'ready': True}
    asyncio.run(run())
