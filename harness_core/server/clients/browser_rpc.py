"""Private Unix-socket RPC between agent plugins and the persistent browser."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import time
import uuid
from pathlib import Path

from .browser import BrowserError
from ..runtime.discovery import ServerLease

MAX_RPC_BYTES = 16 * 1024 * 1024
SERVICE_METHODS = {
    "close_web",
    "close_web_tab",
    "open_harness_ui",
    "open_web",
    "goto",
    "observe",
}
SESSION_METHODS = {
    "back",
    "click",
    "fill",
    "press",
    "select",
    "select_tab",
    "status",
}


def browser_socket_path(runtime_root):
    return Path(runtime_root).expanduser().resolve() / "runtime" / "browser.sock"


def _json_value(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


class BrowserRPCServer:
    def __init__(self, socket_path, service):
        self.socket_path = Path(socket_path)
        self.service = service
        self.server = None
        self.connections = {}
        self.lease = ServerLease(self.socket_path.with_suffix(".lock"))
        self.socket_identity = None

    async def start(self):
        if self.server is not None:
            return self
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lease.acquire()
        try:
            try:
                existing = self.socket_path.lstat()
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if not stat.S_ISSOCK(existing.st_mode):
                    raise BrowserError("browser RPC path is not a socket")
                # Preserve a live daemon from before advisory locking existed.
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_unix_connection(self.socket_path), 1,
                    )
                except (ConnectionRefusedError, FileNotFoundError):
                    self.socket_path.unlink(missing_ok=True)
                else:
                    writer.close()
                    await writer.wait_closed()
                    raise BrowserError("browser RPC socket is already active")
            self.server = await asyncio.start_unix_server(
                self._connected, path=str(self.socket_path), limit=MAX_RPC_BYTES,
            )
            identity = self.socket_path.stat()
            self.socket_identity = (identity.st_dev, identity.st_ino)
            os.chmod(self.socket_path, 0o600)
        except BaseException:
            if self.server is not None:
                await self.close()
            else:
                self.lease.release()
            raise
        return self

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._handle(reader, writer))
        self.connections[task] = writer
        task.add_done_callback(lambda done: self.connections.pop(done, None))

    async def close(self):
        server, self.server = self.server, None
        if server is not None:
            server.close()
        pending = tuple(self.connections)
        for task in pending:
            # A task cancelled before its first step never enters its finally.
            self.connections[task].close()
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if server is not None:
            await server.wait_closed()
        try:
            await self.service.stop()
        finally:
            try:
                identity = self.socket_path.lstat()
                if (identity.st_dev, identity.st_ino) == self.socket_identity:
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass
            finally:
                self.socket_identity = None
                self.lease.release()

    async def _dispatch(self, request):
        scope = request.get("scope")
        method = request.get("method")
        args = request.get("args", [])
        kwargs = request.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise BrowserError("browser RPC args and kwargs must be JSON containers")
        if scope == "service" and method in SERVICE_METHODS:
            operation = getattr(self.service, method)
        elif scope == "session" and method in SESSION_METHODS:
            if not args:
                raise BrowserError("browser session RPC requires a target")
            target, args = args[0], args[1:]
            operation = getattr(self.service.session(target), method)
        else:
            raise BrowserError(f"unsupported browser RPC operation: {scope}.{method}")
        return _json_value(await operation(*args, **kwargs))

    async def _handle(self, reader, writer):
        try:
            await self._respond(reader, writer)
        except (ConnectionError, OSError):
            # A worker may disappear while its result is being sent.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _respond(self, reader, writer):
        request_id = None
        try:
            payload = await reader.readline()
            if not payload or len(payload) > MAX_RPC_BYTES:
                raise BrowserError("invalid browser RPC payload size")
            request = json.loads(payload)
            if not isinstance(request, dict):
                raise BrowserError("browser RPC payload must be an object")
            request_id = request.get("id")
            result = await self._dispatch(request)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as error:  # noqa: BLE001 — RPC translates arbitrary service failures.
            response = {
                "id": request_id,
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            }
        writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        await writer.drain()


class BrowserRPCClient:
    """Synchronous client matching the BrowserController plugin surface."""

    def __init__(self, socket_path, *, timeout=30, connect_timeout=5):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    def _connect(self):
        deadline = time.monotonic() + self.connect_timeout
        last_error = None
        while time.monotonic() < deadline:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(self.timeout)
            try:
                client.connect(str(self.socket_path))
                return client
            except OSError as error:
                last_error = error
                client.close()
                time.sleep(0.05)
        raise BrowserError(f"persistent browser service is unavailable: {last_error}")

    def _request(self, scope, method, args, kwargs):
        request_id = str(uuid.uuid4())
        payload = json.dumps({
            "id": request_id,
            "scope": scope,
            "method": method,
            "args": _json_value(list(args)),
            "kwargs": _json_value(kwargs),
        }, separators=(",", ":")).encode() + b"\n"
        if len(payload) > MAX_RPC_BYTES:
            raise BrowserError("browser RPC request is too large")
        with self._connect() as client:
            client.sendall(payload)
            chunks = bytearray()
            while not chunks.endswith(b"\n"):
                chunk = client.recv(min(65536, MAX_RPC_BYTES - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) >= MAX_RPC_BYTES:
                    raise BrowserError("browser RPC response is too large")
        try:
            response = json.loads(chunks)
        except (TypeError, ValueError) as error:
            raise BrowserError("persistent browser returned invalid JSON") from error
        if response.get("id") != request_id:
            raise BrowserError("persistent browser response correlation mismatch")
        if not response.get("ok"):
            raise BrowserError(response.get("error") or "persistent browser operation failed")
        return response.get("result")

    def call(self, method, *args, **kwargs):
        return self._request("service", method, args, kwargs)

    def session_call(self, target, method, *args, **kwargs):
        return self._request("session", method, (target, *args), kwargs)

    def close(self):
        # The daemon, not an individual worker/plugin, owns Chromium.
        return None
