"""Optional authenticated host boundary with separately authorized capabilities.

Bind only an explicitly chosen private address (loopback for local clients, a
private Docker bridge for containers). This is not an Internet/TLS transport.
The host registers its build recipe, test options and optional browser service.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import json
import socket
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

from .image import DeploymentError, image_id

MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class HostEndpoint:
    address: tuple[str, int]
    token: str = field(repr=False)
    browser_token: str = field(repr=False)
    execution_token: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if len(self.address) != 2 or not isinstance(self.address[0], str) or not 1 <= self.address[1] <= 65535:
            raise ValueError("a host address and port are required")
        if not self.token or not self.browser_token or self.token == self.browser_token:
            raise ValueError("distinct host-control and browser-RPC tokens are required")
        if self.execution_token is not None and (
                not self.execution_token or self.execution_token in {self.token, self.browser_token}):
            raise ValueError("execution requires a distinct nonempty credential")
        object.__setattr__(self, "address", tuple(self.address))

    def settings(self):
        return {"address": self.address, "token": self.token, "browser_token": self.browser_token,
                "execution_token": self.execution_token}

    def _request(self, operation, payload, token):
        request = json.dumps({
            "token": token, "operation": operation, "payload": payload or {},
        }, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(request) > MAX_BYTES:
            raise DeploymentError("host request is too large")
        return request

    @staticmethod
    def _receive(connection, *, buffering=-1):
        # Unbuffered: a stream handshake must not consume worker-protocol bytes.
        with connection.makefile("rb", buffering=buffering) as stream:
            response = stream.readline(MAX_BYTES + 1)
        if not response.endswith(b"\n") or len(response) > MAX_BYTES:
            raise DeploymentError("invalid host response size")
        response = json.loads(response)
        if not response.get("ok"):
            raise DeploymentError(response.get("error", "host operation failed"))
        return response["result"]

    def _call(self, operation, payload, token, timeout):
        request = self._request(operation, payload, token)
        try:
            with socket.create_connection(self.address, timeout=5) as connection:
                connection.settimeout(timeout)
                connection.sendall(request)
                return self._receive(connection)
        except (OSError, ValueError) as error:
            raise DeploymentError(f"host service unavailable ({type(error).__name__})") from error

    def call(self, operation, payload=None, *, browser=False, timeout=900):
        return self._call(operation, payload, self.browser_token if browser else self.token, timeout)

    def _execution_credential(self):
        if self.execution_token is None:
            raise DeploymentError("host execution is not configured")
        return self.execution_token

    def open_process(self, name):
        request = self._request("execution.open", {"recipe": name}, self._execution_credential())
        connection = socket.create_connection(self.address, timeout=5)
        try:
            connection.sendall(request)
            handle = self._receive(connection, buffering=0)
            connection.settimeout(None)
            return connection, handle
        except BaseException:
            connection.close()
            raise

    def process_control(self, handle, action, **payload):
        return self._call("execution.control", {"handle": handle, "action": action, **payload},
                          self._execution_credential(), 5)


class HostServices:
    """Host-owned recipes and browser; accepted control calls finish on disconnect.

    ``build`` is a zero-argument callable returning an immutable image ID. It
    owns wheel creation/selection; the caller cannot choose a host path/command.
    Validation uses only host-configured options and an optional session COPY.
    Restart is deliberately absent: use the backend's durable turn-boundary
    restart callback, which exits 75 for the external supervisor.
    """

    def __init__(self, deployment, *, token, browser_token, build=None, browser=None, session=None, options=None,
                 execution=None, execution_token=None):
        if not token or not browser_token or token == browser_token:
            raise ValueError("distinct host-control and browser-RPC tokens are required")
        if (execution is None) != (execution_token is None):
            raise ValueError("execution service and its credential must be configured together")
        if execution_token is not None and (not execution_token or execution_token in {token, browser_token}):
            raise ValueError("execution requires a distinct nonempty credential")
        from .deployment import application_options

        self.deployment, self.build, self.browser = deployment, build, browser
        self.token, self.browser_token = token, browser_token
        self.execution, self.execution_token = execution, execution_token
        self.session, self.options = session, application_options(options)
        self.server = None
        self.connections = set()
        self.busy = asyncio.Lock()

    async def start(self, address):
        host, port = address
        ip = ipaddress.ip_address(host)
        if not ip.is_private or ip.is_unspecified or ip.is_multicast:
            raise ValueError("host services require an explicit private bind address")
        if self.server is not None:
            raise RuntimeError("host services already started")
        self.server = await asyncio.start_server(self._connected, host, port, limit=MAX_BYTES)
        return self.server.sockets[0].getsockname()[:2]

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._respond(reader, writer))
        self.connections.add(task)
        task.add_done_callback(self.connections.discard)

    async def close(self):
        server, self.server = self.server, None
        if server is not None:
            server.close()
        if self.execution is not None:
            await self.execution.close()
        # Bounded Docker calls own their cleanup. Disconnect never interrupts a
        # release-state mutation or leaves a test container without its finally.
        await asyncio.gather(*tuple(self.connections), return_exceptions=True)
        if server is not None:
            await server.wait_closed()

    def _authorize(self, request):
        operation, payload = request.get("operation"), request.get("payload", {})
        token = request.get("token")
        expected = (self.execution_token if operation in {"execution.open", "execution.control"}
                    else self.browser_token if operation == "browser" else self.token)
        if expected is None or not isinstance(token, str) or not hmac.compare_digest(token, expected):
            raise DeploymentError("unauthorized host request")
        if not isinstance(payload, dict):
            raise ValueError("host payload must be an object")
        return operation, payload

    async def _dispatch(self, request):
        operation, payload = self._authorize(request)
        if operation == "execution.control" and {"handle", "action"} <= payload.keys():
            return await asyncio.to_thread(self.execution.control, **payload)
        if operation == "browser":
            if self.browser is None:
                raise DeploymentError("host browser is unavailable")
            from ..server.clients.browser_rpc import dispatch_browser

            if payload.get("scope") == "service" and payload.get("method") == "observe":
                args = payload.get("args")
                if not isinstance(args, list) or len(args) != 2:
                    raise ValueError("observe requires a target and screenshot path")
                # The backend path is NEVER a host write capability. Transfer
                # frozen bytes instead of sharing the backend filesystem.
                with tempfile.TemporaryDirectory(prefix="host-observation-") as directory:
                    screenshot = Path(directory) / "observation.png"
                    snapshot, _ = await dispatch_browser(self.browser, {
                        **payload, "args": [args[0], str(screenshot)],
                    })
                    return {"snapshot": snapshot, "screenshot": base64.b64encode(screenshot.read_bytes()).decode("ascii")}
            return await dispatch_browser(self.browser, payload)
        if operation == "status" and not payload:
            return self.deployment.status()
        if operation not in {"build", "test", "select"}:
            raise DeploymentError("unsupported host operation")
        if self.busy.locked():
            raise DeploymentError("a host deployment operation is already running")
        async with self.busy:
            if operation == "build" and not payload and self.build is not None:
                return image_id(await asyncio.to_thread(self.build))
            if operation in {"test", "select"} and set(payload) == {"image"}:
                identifier = image_id(payload["image"])
                if operation == "test":
                    return await asyncio.to_thread(self.deployment.test, identifier, session=self.session, options=self.options)
                return await asyncio.to_thread(self.deployment.select, identifier)
            raise DeploymentError("invalid or unavailable host operation")

    async def _respond(self, reader, writer):
        streaming = False
        try:
            try:
                payload = await asyncio.wait_for(reader.readline(), 5)
                if not payload.endswith(b"\n") or len(payload) > MAX_BYTES:
                    raise ValueError("invalid host request size")
                request = json.loads(payload)
                if not isinstance(request, dict):
                    raise ValueError("host request must be an object")
                operation, arguments = self._authorize(request)
                if operation == "execution.open":
                    if set(arguments) != {"recipe"} or not isinstance(arguments["recipe"], str):
                        raise ValueError("execution.open requires one recipe name")
                    handle, process = self.execution.open(arguments["recipe"])
                    try:
                        writer.write(json.dumps({"ok": True, "result": handle}).encode() + b"\n")
                        await writer.drain()
                        streaming = True
                        await self.execution.relay(handle, process, reader, writer)
                    finally:
                        await asyncio.to_thread(self.execution.retire, handle)
                    return
                response = {"ok": True, "result": await self._dispatch(request)}
            except Exception as error:
                if streaming:
                    return  # The plugin owns framing after the successful handshake.
                # Diagnostics may contain factory output. Never send credentials.
                message = str(error)
                for credential in (self.token, self.browser_token, self.execution_token):
                    if credential:
                        message = message.replace(credential, "[redacted]")
                response = {"ok": False, "error": f"{type(error).__name__}: {message}"}
            encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
            if len(encoded) > MAX_BYTES:
                encoded = b'{"ok":false,"error":"host response too large"}\n'
            writer.write(encoded)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
