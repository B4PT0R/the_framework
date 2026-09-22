"""Private client pairing and resumable remote event transport."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import os
import re
import secrets
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from modict import modict

from ..api.websockets import serve_event_stream_until_disconnect

from ...agent.runtime.protocol import (
    ApplicationCall,
    ApplicationCancel,
    ApplicationRequest,
    CommandAccepted,
    CommandCompleted,
    CommandEvent,
    CommandFailed,
)

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
PAIRING_LIFETIME = timedelta(minutes=5)
LEASE_LIFETIME = timedelta(seconds=30)
EVENT_HEARTBEAT_SECONDS = 20
MAX_PAIRING_ATTEMPTS = 5
MAX_COMMAND_RECEIPTS = 1024
MAX_NOTIFICATION_EVENTS = 256
_PLATFORM_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CLOSE_SUBSCRIBER = object()


class RemoteClientPolicy(modict):
    """Application-owned remote scopes, capabilities, and lease semantics."""

    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)
    default_scopes: tuple[str, ...] = ()
    platform_scopes: dict[str, tuple[str, ...]] | None = None
    capabilities: frozenset[str] = frozenset()
    exclusive_capabilities: frozenset[str] = frozenset()
    routed_capabilities: frozenset[str] = frozenset()
    default_lease_capabilities: frozenset[str] = frozenset()
    unleased_events: frozenset[str] = frozenset()
    event_capabilities: dict[str, frozenset[str]] | None = None

    @modict.model_validator(mode="after")
    def validate_capabilities(self):
        for name in ("exclusive_capabilities", "routed_capabilities", "default_lease_capabilities"):
            if not getattr(self, name) <= self.capabilities:
                raise ValueError(f"{name} must be declared in capabilities")
        if not self.routed_capabilities <= self.exclusive_capabilities:
            raise ValueError("routed_capabilities require exclusive capability leases")
        if not self.default_lease_capabilities <= self.exclusive_capabilities:
            raise ValueError("default_lease_capabilities require exclusive capability leases")
        for event, capabilities in (self.event_capabilities or {}).items():
            if event in self.unleased_events:
                raise ValueError("an event cannot be both unleased and capability-bound")
            if not capabilities or not capabilities <= self.exclusive_capabilities:
                raise ValueError("event capabilities require declared exclusive leases")

    def validate_event_capability(self, name, capability):
        allowed = (self.event_capabilities or {}).get(name)
        if allowed is not None and capability not in allowed:
            raise ValueError(f"event {name} requires one of: {', '.join(sorted(allowed))}")

    def scopes_for(self, platform):
        configured = self.platform_scopes or {}
        return tuple(dict.fromkeys((*self.default_scopes, *configured.get(platform, ()))))

    @classmethod
    def compose(cls, *contributions):
        """Combine startup-installed feature policies into one validated contract."""
        scopes = []
        platform_scopes = {}
        capabilities = set()
        exclusive = set()
        routed = set()
        default_leases = set()
        unleased_events = set()
        event_capabilities = {}
        for contribution in contributions:
            if not isinstance(contribution, cls):
                raise TypeError("remote policy contribution must be a RemoteClientPolicy")
            duplicate_capabilities = capabilities & contribution.capabilities
            if duplicate_capabilities:
                raise ValueError(
                    "duplicate remote capabilities: "
                    + ", ".join(sorted(duplicate_capabilities))
                )
            duplicate_events = (
                unleased_events | set(event_capabilities)
            ) & (
                set(contribution.unleased_events)
                | set(contribution.event_capabilities or {})
            )
            if duplicate_events:
                raise ValueError(
                    "duplicate remote events: " + ", ".join(sorted(duplicate_events))
                )
            scopes.extend(contribution.default_scopes)
            for platform, values in (contribution.platform_scopes or {}).items():
                platform_scopes.setdefault(platform, []).extend(values)
            capabilities.update(contribution.capabilities)
            exclusive.update(contribution.exclusive_capabilities)
            routed.update(contribution.routed_capabilities)
            default_leases.update(contribution.default_lease_capabilities)
            unleased_events.update(contribution.unleased_events)
            event_capabilities.update(contribution.event_capabilities or {})
        return cls(
            default_scopes=tuple(dict.fromkeys(scopes)),
            platform_scopes={
                platform: tuple(dict.fromkeys(values))
                for platform, values in platform_scopes.items()
            },
            capabilities=frozenset(capabilities),
            exclusive_capabilities=frozenset(exclusive),
            routed_capabilities=frozenset(routed),
            default_lease_capabilities=frozenset(default_leases),
            unleased_events=frozenset(unleased_events),
            event_capabilities=event_capabilities,
        )


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class RemoteClientStore:
    # Activity is live in memory; only this non-authoritative timestamp may lag
    # on disk. Credential issuance/revocation always call save immediately.
    activity_persist_interval = 60.0

    def __init__(self, path, *, policy=None):
        self.path = Path(path)
        self.policy = policy or RemoteClientPolicy()
        self.clients = {}
        self._last_activity_save = None
        self.load()

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if payload.get("version") != PROTOCOL_VERSION:
            raise ValueError("unsupported remote client store version")
        clients = payload.get("clients")
        if not isinstance(clients, list):
            raise ValueError("remote client store has no client list")
        migrated = False
        for item in clients:
            if item.pop("notification_registration", None) is not None:
                migrated = True
        self.clients = {item["id"]: item for item in clients}
        os.chmod(self.path, 0o600)
        if migrated:
            self.save()

    def save(self):
        _atomic_json(self.path, {
            "version": PROTOCOL_VERSION,
            "clients": sorted(self.clients.values(), key=lambda item: item["created_at"]),
        })

    def issue(self, name, platform="android"):
        token = secrets.token_urlsafe(48)
        now = _iso(_utc_now())
        client = {
            "id": str(uuid4()),
            "name": name,
            "platform": platform,
            "scopes": list(self.policy.scopes_for(platform)),
            "token_hash": _token_hash(token),
            "created_at": now,
            "last_seen_at": now,
        }
        self.clients[client["id"]] = client
        self.save()
        return self.public(client), token

    def authenticate(self, token):
        if not isinstance(token, str) or not token:
            return None
        digest = _token_hash(token)
        for client in self.clients.values():
            if hmac.compare_digest(client["token_hash"], digest):
                client["last_seen_at"] = _iso(_utc_now())
                now = time.monotonic()
                if self._last_activity_save is None \
                        or now - self._last_activity_save >= self.activity_persist_interval:
                    self.save()
                    self._last_activity_save = now
                return client
        return None

    def revoke(self, client_id):
        removed = self.clients.pop(client_id, None)
        if removed is not None:
            self.save()
        return removed is not None

    def list(self):
        return [self.public(item) for item in self.clients.values()]

    @staticmethod
    def public(client):
        return {
            key: value
            for key, value in client.items()
            if key not in {"token_hash", "notification_registration"}
        }


class PendingPairing(modict):
    _config = modict.config(strict=True, extra="forbid", auto_convert=False)

    id: str
    secret_hash: str
    endpoint: str
    created_at: datetime
    expires_at: datetime
    failed_attempts: int = 0


class ClientApplicationConnection(modict):
    _config = modict.config(strict=True, extra="forbid", auto_convert=False)

    client_id: str
    websocket: object
    outgoing: asyncio.Queue
    connection_id: str = ""
    available_capabilities: frozenset = frozenset()
    runtime: dict | None = None
    revoked: bool = False


class CapabilityLease(modict):
    _config = modict.config(strict=True, extra="forbid", auto_convert=False)

    lease_id: str
    capability: str
    client_id: str
    connection_id: str
    granted_at: datetime
    expires_at: datetime
    generation: int

    def public(self):
        return {
            "lease_id": self.lease_id,
            "capability": self.capability,
            "client_id": self.client_id,
            "granted_at": _iso(self.granted_at),
            "expires_at": _iso(self.expires_at),
            "generation": self.generation,
        }


class ClientRemoteService:
    connection_type = ClientApplicationConnection

    def on_application_event(self, connection, payload):
        """Observe an event only after its lease and capability are validated."""

    async def on_capabilities_changed(self, connection):
        """Reconcile application state after authority is released or expires."""

    def __init__(
        self, harness, path, *, journal_size=2048,
        event_heartbeat_seconds=EVENT_HEARTBEAT_SECONDS,
        pairing_scheme="agent",
        application_subprotocol="agent-application-v1",
        event_subprotocol="agent-remote-v1",
        policy=None, message_notification_listener=None,
    ):
        if not re.fullmatch(r"[a-z][a-z0-9+.-]*", pairing_scheme):
            raise ValueError("pairing_scheme must be a valid lowercase URI scheme")
        self.harness = harness
        self.policy = policy or RemoteClientPolicy()
        self._policy_locked = False
        self.clients = RemoteClientStore(path, policy=self.policy)
        self.receipts_path = Path(path).with_name("remote-command-receipts.json")
        self.pairings = {}
        self.journal = deque(maxlen=journal_size)
        self.sequence = 0
        self.instance_id = str(uuid4())
        self.subscribers = set()
        self.event_connections = {}
        self.relay_task = None
        self.lease_task = None
        self.application_connections = {}
        self.capability_leases = {}
        self.lease_lock = asyncio.Lock()
        self.lease_generations = {}
        self.routed_calls = {}
        self.command_receipts = OrderedDict()
        self.command_lock = asyncio.Lock()
        self.event_heartbeat_seconds = event_heartbeat_seconds
        self.pairing_scheme = pairing_scheme
        self.application_subprotocol = application_subprotocol
        self.event_subprotocol = event_subprotocol
        self.application_payload_adapter = None
        self.event_payload_adapter = None
        self.message_notification_listener = message_notification_listener
        self.notification_buffers = {}
        self.notification_tasks = set()
        self.notification_sequence = 0
        self.notification_journal = deque(maxlen=MAX_NOTIFICATION_EVENTS)
        self.notification_ids = OrderedDict()
        self.notification_condition = asyncio.Condition()
        self._load_command_receipts()

    def _load_command_receipts(self):
        try:
            payload = json.loads(self.receipts_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if payload.get("version") != PROTOCOL_VERSION or not isinstance(payload.get("receipts"), list):
            raise ValueError("unsupported remote command receipt store")
        for item in payload["receipts"][-MAX_COMMAND_RECEIPTS:]:
            self.command_receipts[(item["client_id"], item["command_id"])] = {
                "fingerprint": item["fingerprint"], "response": item["response"],
            }
        os.chmod(self.receipts_path, 0o600)

    def _save_command_receipts(self):
        _atomic_json(self.receipts_path, {
            "version": PROTOCOL_VERSION,
            "receipts": [
                {
                    "client_id": client_id, "command_id": command_id,
                    "fingerprint": receipt["fingerprint"], "response": receipt["response"],
                }
                for (client_id, command_id), receipt in self.command_receipts.items()
            ],
        })

    async def start(self):
        self._policy_locked = True
        if self.relay_task is None:
            self.relay_task = asyncio.create_task(self._relay())
        if self.lease_task is None:
            self.lease_task = asyncio.create_task(self._lease_watchdog())

    def add_policy(self, contribution):
        """Add one installed feature's policy before any client can connect."""
        if self._policy_locked:
            raise RuntimeError("remote client policy is fixed after startup")
        policy = RemoteClientPolicy.compose(self.policy, contribution)
        self.policy = policy
        self.clients.policy = policy

    async def stop(self):
        sockets = [
            *[socket for sockets in self.event_connections.values() for socket in sockets],
            *[connection.websocket for connection in self.application_connections.values()],
        ]
        await asyncio.gather(
            *(socket.close(code=1001) for socket in sockets), return_exceptions=True,
        )
        if self.relay_task is not None:
            self.relay_task.cancel()
            await asyncio.gather(self.relay_task, return_exceptions=True)
            self.relay_task = None
        if self.lease_task is not None:
            self.lease_task.cancel()
            await asyncio.gather(self.lease_task, return_exceptions=True)
            self.lease_task = None
        for task in tuple(self.notification_tasks):
            task.cancel()
        await asyncio.gather(*self.notification_tasks, return_exceptions=True)
        self.notification_tasks.clear()

    async def _relay(self):
        async for output in self.harness.stream():
            self.sequence += 1
            event = {
                "v": PROTOCOL_VERSION,
                "server_instance_id": self.instance_id,
                "seq": self.sequence,
                "emitted_at": _iso(_utc_now()),
                # Keep modict payloads intact: their Mapping protocol evaluates
                # computed fields during JSON encoding, while to_dict() deliberately
                # preserves the internal Computed placeholders.
                "payload": output,
            }
            self.journal.append(event)
            self._observe_notifications(output)
            for queue in tuple(self.subscribers):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.subscribers.discard(queue)
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(_CLOSE_SUBSCRIBER)

    @staticmethod
    def _event_text(event):
        content = getattr(event, "content", None)
        if not isinstance(content, list):
            return ""
        return "".join(
            str(getattr(part, "text", "") or "")
            for part in content
            if getattr(part, "type", None) in {"input_text", "output_text", "text"}
        ).strip()

    def _observe_notifications(self, output):
        if (
            isinstance(output, CommandAccepted)
            and getattr(output.command, "prompt_kind", None) == "scheduled_alarm"
        ):
            self._schedule_notifications(output.command.id, kind="alarm")
            return
        if isinstance(output, CommandEvent):
            event = output.event
            if event.type == "response.output_text.delta":
                if str(getattr(event, "delta", "") or ""):
                    self.notification_buffers[output.command_id] = True
                return
            if event.type == "agent.response_item.added":
                item = getattr(event, "item", None)
                if (
                    getattr(item, "role", None) == "assistant"
                    and getattr(item, "kind", None) == "realtime"
                ):
                    text = self._event_text(item)
                    if text:
                        self._schedule_notifications(output.command_id, text)
                return
        if isinstance(output, CommandFailed):
            self.notification_buffers.pop(output.command.id, None)
            return
        if isinstance(output, CommandCompleted):
            has_message = self.notification_buffers.pop(output.command.id, False)
            if has_message:
                if getattr(output.command, "prompt_kind", None) != "scheduled_alarm":
                    self._schedule_notifications(output.command.id, kind="message")

    def _schedule_notifications(self, message_id, _text=None, *, kind="message"):
        if kind not in {"message", "alarm"}:
            raise ValueError("notification kind must be message or alarm")
        notification_id = (str(message_id), kind)
        if notification_id in self.notification_ids:
            return
        self.notification_ids[notification_id] = None
        while len(self.notification_ids) > MAX_NOTIFICATION_EVENTS:
            self.notification_ids.popitem(last=False)
        task = asyncio.create_task(
            self._record_notification(str(message_id), kind=kind),
            name=f"remote-notification-event-{message_id}",
        )
        self.notification_tasks.add(task)
        task.add_done_callback(self.notification_tasks.discard)
        if self.message_notification_listener is not None:
            try:
                result = self.message_notification_listener(str(message_id))
                if inspect.isawaitable(result):
                    task = asyncio.create_task(
                        result, name=f"message-notification-{message_id}",
                    )
                    self.notification_tasks.add(task)
                    task.add_done_callback(self.notification_tasks.discard)
            except Exception as error:  # noqa: BLE001 - optional notification channel
                logger.warning("message notification listener failed: %s", error)

    async def _record_notification(self, message_id, *, kind="message"):
        async with self.notification_condition:
            self.notification_sequence += 1
            event = {
                "sequence": self.notification_sequence,
                "server_instance_id": self.instance_id,
                "message_id": message_id,
                "kind": kind,
                "emitted_at": _iso(_utc_now()),
            }
            self.notification_journal.append(event)
            self.notification_condition.notify_all()
            return event

    async def wait_for_notification(
        self, *, after=None, server_instance_id=None, timeout=55,
    ):
        timeout = max(1, min(float(timeout), 55))
        async with self.notification_condition:
            reset = (
                server_instance_id is not None
                and server_instance_id != self.instance_id
            )
            if after is None or reset or after > self.notification_sequence:
                return {
                    "status": "reset",
                    "server_instance_id": self.instance_id,
                    "cursor": self.notification_sequence,
                    "events": [],
                }

            def available():
                return any(
                    event["sequence"] > after
                    for event in self.notification_journal
                )

            if not available():
                try:
                    await asyncio.wait_for(
                        self.notification_condition.wait_for(available),
                        timeout=timeout,
                    )
                except TimeoutError:
                    pass
            events = [
                event for event in self.notification_journal
                if event["sequence"] > after
            ]
            return {
                "status": "events" if events else "idle",
                "server_instance_id": self.instance_id,
                "cursor": events[-1]["sequence"] if events else after,
                "events": events,
            }

    def purge_pairings(self):
        now = _utc_now()
        expired = [pairing_id for pairing_id, pairing in self.pairings.items()
                   if pairing.expires_at <= now]
        for pairing_id in expired:
            self.pairings.pop(pairing_id, None)

    @staticmethod
    def _validate_endpoint(endpoint):
        if not isinstance(endpoint, str):
            raise ValueError("a remote HTTP(S) endpoint is required")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("a valid remote HTTP(S) endpoint is required")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("remote endpoint must not contain credentials, query, or fragment")
        loopback = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "http" and not loopback:
            raise ValueError("non-loopback remote endpoints require HTTPS")
        return endpoint.rstrip("/")

    def create_pairing(self, endpoint):
        self.purge_pairings()
        endpoint = self._validate_endpoint(endpoint)
        pairing_id = secrets.token_urlsafe(18)
        secret = secrets.token_urlsafe(32)
        now = _utc_now()
        pairing = PendingPairing(
            id=pairing_id,
            secret_hash=_token_hash(secret),
            endpoint=endpoint,
            created_at=now,
            expires_at=now + PAIRING_LIFETIME,
        )
        self.pairings[pairing_id] = pairing
        query = urlencode({
            "v": PROTOCOL_VERSION,
            "endpoint": pairing.endpoint,
            "pairing_id": pairing.id,
            "secret": secret,
        })
        return {
            "version": PROTOCOL_VERSION,
            "pairing_id": pairing.id,
            "secret": secret,
            "endpoint": pairing.endpoint,
            "pairing_uri": f"{self.pairing_scheme}://pair?{query}",
            "expires_at": _iso(pairing.expires_at),
        }

    def claim(self, pairing_id, secret, device_name, platform="android"):
        pairing = self.pairings.get(pairing_id)
        if pairing is None:
            raise ValueError("unknown or already claimed pairing")
        if pairing.expires_at <= _utc_now():
            self.pairings.pop(pairing_id, None)
            raise ValueError("pairing has expired")
        if not hmac.compare_digest(pairing.secret_hash, _token_hash(str(secret))):
            pairing.failed_attempts += 1
            if pairing.failed_attempts >= MAX_PAIRING_ATTEMPTS:
                self.pairings.pop(pairing_id, None)
            raise ValueError("invalid pairing secret")
        name = str(device_name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("device_name must contain 1 to 80 characters")
        platform = str(platform or "").strip().lower()
        if not _PLATFORM_PATTERN.fullmatch(platform):
            raise ValueError("platform must be a lowercase identifier of 1 to 32 characters")
        self.pairings.pop(pairing_id, None)
        client, token = self.clients.issue(name, platform)
        return {
            "version": PROTOCOL_VERSION,
            "client": client,
            "access_token": token,
            "endpoint": pairing.endpoint,
        }

    def authenticate(self, token):
        return self.clients.authenticate(token)

    async def submit_idempotent(self, client_id, command_id, payload, submit):
        if not isinstance(command_id, str) or not command_id.strip() or len(command_id) > 128:
            raise ValueError("command_id must contain 1 to 128 characters")
        fingerprint = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        key = (client_id, command_id)
        async with self.command_lock:
            receipt = self.command_receipts.get(key)
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise ValueError("command_id was already used with a different command")
                self.command_receipts.move_to_end(key)
                return {**receipt["response"], "deduplicated": True}
            response = await submit()
            self.command_receipts[key] = {"fingerprint": fingerprint, "response": response}
            while len(self.command_receipts) > MAX_COMMAND_RECEIPTS:
                self.command_receipts.popitem(last=False)
            self._save_command_receipts()
            return response

    async def revoke_client(self, client_id):
        if client_id not in self.clients.clients:
            return False
        event_sockets = tuple(self.event_connections.pop(client_id, ()))
        application = self.application_connections.get(client_id)
        self.clients.revoke(client_id)
        if application is not None:
            application.revoked = True
        try:
            await self.release_capabilities(client_id)
        finally:
            # Application cleanup cannot keep a revoked credential or channel
            # alive. Keep its connection until the hook has seen its state.
            if self.application_connections.get(client_id) is application:
                self.application_connections.pop(client_id, None)
            sockets = list(event_sockets)
            if application is not None:
                sockets.append(application.websocket)
            await asyncio.gather(
                *(socket.close(code=1008, reason="remote client revoked") for socket in sockets),
                return_exceptions=True,
            )
            self.command_receipts = OrderedDict(
                (key, receipt) for key, receipt in self.command_receipts.items()
                if key[0] != client_id
            )
            self._save_command_receipts()
        return True

    def lease_status(self):
        now = _utc_now()
        return {
            "available_capabilities": sorted(self.policy.capabilities),
            "exclusive_capabilities": sorted(self.policy.exclusive_capabilities),
            "leases": [
                lease.public() for lease in sorted(
                    self.capability_leases.values(), key=lambda item: item.capability,
                )
                if lease.expires_at > now
            ],
            "connected_clients": [
                {
                    "client_id": connection.client_id,
                    "available_capabilities": sorted(connection.available_capabilities),
                    "runtime": connection.runtime or {},
                }
                for connection in self.application_connections.values()
            ],
        }

    async def _lease_watchdog(self):
        while True:
            await asyncio.sleep(1)
            await self.expire_capabilities()

    async def expire_capabilities(self):
        async with self.lease_lock:
            return await self._expire_capabilities()

    async def _expire_capabilities(self):
        now = _utc_now()
        expired = [lease for lease in self.capability_leases.values()
                   if lease.expires_at <= now]
        if not expired:
            return self.lease_status()
        for lease in expired:
            self.capability_leases.pop(lease.capability, None)
        for lease in expired:
            await self._reject_client_calls(
                lease.client_id, "remote capability lease expired",
                capabilities=[lease.capability],
            )
        state = {"type": "capability_lease_state", "reason": "expired", **self.lease_status()}
        for client_id in {lease.client_id for lease in expired}:
            connection = self.application_connections.get(client_id)
            if connection is not None:
                await self.on_capabilities_changed(connection)
                try:
                    connection.outgoing.put_nowait(state)
                except asyncio.QueueFull:
                    # Expiry must not wait for a stalled client's send queue.
                    # Closing its channel forces resynchronization and allows
                    # the receiver's finally block to release remaining leases.
                    try:
                        await asyncio.wait_for(connection.websocket.close(
                            code=1013, reason="remote application backpressure",
                        ), timeout=1)
                    except (TimeoutError, OSError, RuntimeError):
                        logger.warning("unable to close stalled remote application channel")
        return self.lease_status()

    async def acquire_capabilities(self, client_id, capabilities):
        async with self.lease_lock:
            return await self._acquire_capabilities(client_id, capabilities)

    async def _acquire_capabilities(self, client_id, capabilities):
        connection = self.application_connections.get(client_id)
        if connection is None or connection.revoked:
            raise ValueError("application channel must be connected before acquiring capabilities")
        requested = frozenset(capabilities)
        if not requested or not requested <= self.policy.capabilities:
            raise ValueError("one or more remote capabilities are unknown")
        if connection.available_capabilities and not requested <= connection.available_capabilities:
            raise ValueError("client did not announce all requested capabilities")
        await self._expire_capabilities()
        if connection.revoked or self.application_connections.get(client_id) is not connection:
            raise ValueError("application channel changed while acquiring capabilities")
        for capability in requested & self.policy.exclusive_capabilities:
            current = self.capability_leases.get(capability)
            if current is not None and (
                current.client_id != client_id or current.connection_id != connection.connection_id
            ):
                raise ValueError(f"capability {capability} is already owned by another client")
        now = _utc_now()
        for capability in requested & self.policy.exclusive_capabilities:
            current = self.capability_leases.get(capability)
            if current is not None:
                current.expires_at = now + LEASE_LIFETIME
                continue
            generation = self.lease_generations.get(capability, 0) + 1
            self.lease_generations[capability] = generation
            self.capability_leases[capability] = CapabilityLease(
                lease_id=str(uuid4()), capability=capability, client_id=client_id,
                connection_id=connection.connection_id, granted_at=now,
                expires_at=now + LEASE_LIFETIME, generation=generation,
            )
        return self.lease_status()

    async def renew_capabilities(self, client_id, connection_id):
        async with self.lease_lock:
            await self._expire_capabilities()
            now = _utc_now()
            for lease in self.capability_leases.values():
                if lease.client_id == client_id and lease.connection_id == connection_id:
                    lease.expires_at = now + LEASE_LIFETIME
            return self.lease_status()

    def validate_lease_payload(self, client_id, connection_id, lease_payload):
        if not isinstance(lease_payload, dict):
            raise ValueError("application payload requires lease metadata")
        capability = lease_payload.get("capability")
        current = self.capability_leases.get(capability)
        if current is None or current.expires_at <= _utc_now() or current.client_id != client_id \
                or current.connection_id != connection_id \
                or current.lease_id != lease_payload.get("lease_id") \
                or current.generation != lease_payload.get("generation"):
            raise ValueError("application payload carries a stale capability lease")
        return current

    def validate_result_route(self, call_id, lease):
        route = self.routed_calls.get(call_id)
        if route is None or route["client_id"] != lease.client_id \
                or route["capability"] != lease.capability \
                or route["generation"] != lease.generation \
                or route["lease"]["lease_id"] != lease.lease_id:
            raise ValueError("application result does not match an active routed call")

    async def release_capabilities(self, client_id, capabilities=None, *, connection_id=None):
        async with self.lease_lock:
            return await self._release_capabilities(client_id, capabilities, connection_id=connection_id)

    async def _release_capabilities(self, client_id, capabilities=None, *, connection_id=None):
        selected = frozenset(capabilities) if capabilities is not None else None
        released = []
        for capability, lease in tuple(self.capability_leases.items()):
            if lease.client_id != client_id:
                continue
            if connection_id is not None and lease.connection_id != connection_id:
                continue
            if selected is not None and capability not in selected:
                continue
            self.capability_leases.pop(capability, None)
            released.append(capability)
        if released:
            await self._reject_client_calls(
                client_id, "remote capability lease was released", capabilities=released,
            )
            connection = self.application_connections.get(client_id)
            if connection is not None:
                await self.on_capabilities_changed(connection)
        return self.lease_status()


    async def _reject_client_calls(self, client_id, reason, capabilities=None):
        capabilities = frozenset(capabilities) if capabilities is not None else None
        call_ids = [
            call_id for call_id, route in self.routed_calls.items()
            if route["client_id"] == client_id
            and (capabilities is None or route["capability"] in capabilities)
        ]
        for call_id in call_ids:
            self.routed_calls.pop(call_id, None)
            await self.harness.application.reject(call_id, reason)

    async def route(self, output):
        if isinstance(output, ApplicationCancel):
            route = self.routed_calls.pop(output.call_id, None)
            connection = self.application_connections.get(route["client_id"]) if route else None
            if connection is None:
                return False
            await connection.outgoing.put({
                "type": "application_cancel", "call_id": output.call_id,
                "lease": route["lease"],
            })
            return True
        if not isinstance(output, ApplicationCall):
            return False
        if output.request.capability not in self.policy.routed_capabilities:
            return False
        await self.expire_capabilities()
        lease = self.capability_leases.get(output.request.capability)
        connection = self.application_connections.get(lease.client_id) if lease else None
        if lease is None or connection is None or connection.revoked \
                or connection.connection_id != lease.connection_id:
            return False
        request = output.request
        if self.application_payload_adapter is not None:
            try:
                payload = self.application_payload_adapter(
                    lease.client_id,
                    request.capability,
                    request.method,
                    request.payload,
                )
                if asyncio.iscoroutine(payload):
                    payload = await payload
                if payload is not request.payload:
                    request = ApplicationRequest(
                        id=request.id,
                        capability=request.capability,
                        method=request.method,
                        payload=payload,
                        timeout_ms=request.timeout_ms,
                    )
            except (PermissionError, ValueError) as error:
                await self.harness.application.reject(output.request.id, str(error))
                return True
        public_lease = lease.public()
        if connection.revoked or self.application_connections.get(lease.client_id) is not connection \
                or self.capability_leases.get(lease.capability) is not lease \
                or lease.expires_at <= _utc_now():
            await self.harness.application.reject(
                request.id, "remote capability lease changed during request preparation",
            )
            return True
        # Queue insertion must not yield after authority validation. A slow
        # client fails this request instead of blocking routing/lease cleanup.
        try:
            connection.outgoing.put_nowait({
                "type": "application_call", "request": request, "lease": public_lease,
            })
        except asyncio.QueueFull:
            await self.harness.application.reject(request.id, "remote application queue is full")
            return True
        self.routed_calls[output.request.id] = {
            "client_id": lease.client_id,
            "capability": lease.capability,
            "generation": lease.generation,
            "lease": public_lease,
        }
        return True

    async def serve_application(self, websocket, client, *, subprotocol=None):
        client_id = client["id"]
        if client_id not in self.clients.clients:
            await websocket.close(code=1008, reason="remote client revoked")
            return
        if client_id in self.application_connections:
            await websocket.close(code=1013)
            return
        connection = self.connection_type(
            client_id=client_id, websocket=websocket, outgoing=asyncio.Queue(128), connection_id=str(uuid4()),
        )
        self.application_connections[client_id] = connection
        await websocket.accept(subprotocol=subprotocol or self.application_subprotocol)

        async def send():
            try:
                while True:
                    await websocket.send_json(await connection.outgoing.get())
            except asyncio.CancelledError:
                return

        async def receive():
            while True:
                payload = await websocket.receive_json()
                if connection.revoked:
                    return
                message_type = payload.get("type")
                if message_type == "client_hello":
                    announced = payload.get("available_capabilities", [])
                    if not isinstance(announced, list) or any(
                        not isinstance(capability, str) for capability in announced
                    ) or not frozenset(announced) <= self.policy.capabilities:
                        raise ValueError("client announced unknown capabilities")
                    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
                    if len(json.dumps(runtime, separators=(",", ":"))) > 8_192:
                        raise ValueError("client runtime description is too large")
                    connection.available_capabilities = frozenset(announced)
                    connection.runtime = runtime
                    await connection.outgoing.put({
                        "type": "client_hello_ack", "connection_id": connection.connection_id,
                        "client_id": client_id,
                        **self.lease_status(),
                    })
                    continue
                if message_type == "capability_lease":
                    action = payload.get("action")
                    capabilities = payload.get(
                        "capabilities",
                        sorted(self.policy.default_lease_capabilities),
                    )
                    if not isinstance(capabilities, list):
                        raise ValueError("capabilities must be an array")
                    try:
                        if action == "acquire":
                            state = await self.acquire_capabilities(client_id, capabilities)
                        elif action == "release":
                            state = await self.release_capabilities(
                                client_id, capabilities, connection_id=connection.connection_id,
                            )
                        else:
                            raise ValueError(
                                "capability lease action must be acquire or release"
                            )
                    except ValueError as error:
                        await connection.outgoing.put({
                            "type": "capability_lease_state",
                            "status": "denied",
                            "requested_capabilities": capabilities,
                            "error": str(error),
                            **self.lease_status(),
                        })
                    else:
                        await connection.outgoing.put({
                            "type": "capability_lease_state", "status": "accepted", **state,
                        })
                    continue
                if message_type == "heartbeat":
                    state = await self.renew_capabilities(client_id, connection.connection_id)
                    await connection.outgoing.put({
                        "type": "heartbeat_ack", "at": _iso(_utc_now()), **state,
                    })
                    continue
                if message_type == "application_event" and payload.get("name") in self.policy.unleased_events:
                    response = await self.harness.application.accept(
                        payload,
                        responses=connection.outgoing,
                    )
                    if response is not None:
                        await connection.outgoing.put(response)
                    continue
                if message_type in {"application_event", "application_result"}:
                    current = self.validate_lease_payload(
                        client_id, connection.connection_id, payload.get("lease"),
                    )
                    if message_type == "application_event":
                        self.policy.validate_event_capability(payload.get("name"), current.capability)
                    if message_type == "application_event":
                        self.on_application_event(connection, payload)
                    if message_type == "application_result":
                        self.validate_result_route(payload.get("id"), current)
                response = await self.harness.application.accept(
                    {
                        key: value for key, value in payload.items()
                        if key != "lease"
                    },
                    responses=connection.outgoing,
                )
                call_id = payload.get("id")
                if message_type == "application_result" and call_id:
                    self.routed_calls.pop(call_id, None)
                if response is not None:
                    await connection.outgoing.put(response)

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        try:
            try:
                done, pending = await asyncio.wait(
                    (sender, receiver), return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                done, pending = set(), {sender, receiver}
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                error = task.exception()
                if error is not None:
                    raise error
        finally:
            await self.release_capabilities(
                client_id, connection_id=connection.connection_id,
            )
            if self.application_connections.get(client_id) is connection:
                self.application_connections.pop(client_id, None)

    async def stream(self, after=0, server_instance_id=None):
        queue = asyncio.Queue(512)
        oldest = self.journal[0]["seq"] if self.journal else self.sequence + 1
        reset_reason = None
        if server_instance_id and server_instance_id != self.instance_id:
            reset_reason = "server_restarted"
        elif after > self.sequence:
            reset_reason = "server_restarted"
        elif after < oldest - 1:
            reset_reason = "cursor_expired"
        if reset_reason:
            yield {
                "v": PROTOCOL_VERSION,
                "server_instance_id": self.instance_id,
                "seq": self.sequence,
                "emitted_at": _iso(_utc_now()),
                "payload": {"type": "remote_reset_required", "reason": reset_reason},
            }
        else:
            for event in tuple(self.journal):
                if event["seq"] > after:
                    yield event
        self.subscribers.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self.event_heartbeat_seconds,
                    )
                except TimeoutError:
                    yield {
                        "v": PROTOCOL_VERSION,
                        "server_instance_id": self.instance_id,
                        "seq": self.sequence,
                        "emitted_at": _iso(_utc_now()),
                        "payload": {"type": "heartbeat"},
                    }
                    continue
                if event is _CLOSE_SUBSCRIBER:
                    return
                yield event
        finally:
            self.subscribers.discard(queue)

    async def serve_events(
        self,
        websocket,
        client,
        after=0,
        server_instance_id=None,
        *,
        subprotocol=None,
    ):
        client_id = client["id"]
        sockets = self.event_connections.setdefault(client_id, set())
        sockets.add(websocket)
        await websocket.accept(subprotocol=subprotocol or self.event_subprotocol)

        async def send_events():
            async for event in self.stream(after, server_instance_id):
                outgoing = event
                if self.event_payload_adapter is not None:
                    outgoing = self.event_payload_adapter(client_id, event)
                    if asyncio.iscoroutine(outgoing):
                        outgoing = await outgoing
                await websocket.send_json(outgoing)

        try:
            await serve_event_stream_until_disconnect(websocket, send_events)
        finally:
            sockets.discard(websocket)
            if not sockets:
                self.event_connections.pop(client_id, None)
