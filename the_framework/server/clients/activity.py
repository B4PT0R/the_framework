"""Session-local, non-replayed operations for the last participating client."""

import asyncio
import re
from uuid import uuid4

_INSTANCE = re.compile(r"[a-zA-Z0-9_-]{1,100}\Z")


def client_key(principal_id, instance):
    """The authenticated principal namespaces the public, per-page instance ID."""
    if principal_id and isinstance(instance, str) and _INSTANCE.fullmatch(instance):
        return (principal_id, instance)
    return None


def request_client(call):
    if call is None or call.principal is None:
        return None
    return client_key(call.principal.id, call.request.headers.get("x-client-instance"))


class ActiveClients:
    """Connectivity never selects a client; only accepted conversation input does.

    No persistence, broadcast, fallback or offline queue. A reconnect may restore
    the same target, but cannot replay an operation. Preview sockets never join.
    """

    def __init__(self):
        self.active = None
        self.connections = {}
        self.pending = {}

    def connect(self, key, socket):
        if key is None:
            return
        if key in self.connections:
            raise RuntimeError("This client instance is already connected")
        self.connections[key] = socket

    def disconnect(self, key, socket):
        if self.connections.get(key) is socket:
            self.connections.pop(key)
        self._settle(socket, "disconnected")

    def _settle(self, socket, status):
        for (connection, _), future in tuple(self.pending.items()):
            if connection is socket and not future.done():
                future.set_result({"status": status})

    async def activate(self, key):
        previous_key = self.active
        previous = self.connections.get(previous_key)
        changed = key != self.active
        self.active = key  # Unknown input clears the old target, never defaults home.
        if changed and previous is not None:
            self._settle(previous, "inactive")
            try:
                await asyncio.wait_for(
                    previous.send_json({"type": "client_deactivated"}), 1)
            except Exception:
                self.disconnect(previous_key, previous)

    def receive(self, socket, payload):
        if not isinstance(payload, dict) or payload.get("type") != "client_operation_result":
            return
        if not isinstance(payload.get("id"), str):
            return
        future = self.pending.get((socket, payload["id"]))
        if future is not None and not future.done():
            status = payload.get("status")
            if status in {"playing", "blocked", "failed", "cancelled", "busy"}:
                future.set_result({"status": status})

    async def request(self, operation, payload, *, timeout=10):
        key = self.active
        socket = self.connections.get(key)
        if socket is None:
            return {"status": "unavailable"}
        identifier = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[(socket, identifier)] = future
        try:
            async with asyncio.timeout(timeout):
                await socket.send_json({
                    "type": "client_operation", "id": identifier,
                    "operation": operation, "payload": payload,
                })
                result = await future
            return {**result, "client": {"principal": key[0], "instance": key[1]}}
        except TimeoutError:
            # No retry: the client might already have begun playback.
            return {"status": "unconfirmed"}
        except (OSError, RuntimeError):
            self.disconnect(key, socket)
            return {"status": "disconnected"}
        finally:
            self.pending.pop((socket, identifier), None)
