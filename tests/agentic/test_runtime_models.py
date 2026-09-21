"""Runtime records retain identity; declarations validate complete snapshots."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import Request
from modict import modict

from core.agent import AgentSpec, SessionPolicy
from core.agent.runtime.agentic_loop import PendingSteering
from core.server.clients.browser import BrowserSession
from core.server.clients.remote import (
    CapabilityLease, ClientApplicationConnection, PendingPairing,
)
from core.server.composition.application import AgentApplication, Extension, PluginSpec
from core.server.api.endpoints import EndpointContext, Principal
from core.server.composition.services import ServiceSpec
from core.server.clients.surfaces import ClientSurface
from core.server.api.websockets import WebSocketEndpoint


@pytest.mark.parametrize("make", [
    lambda: AgentSpec(name="main"),
    lambda: Extension(name="runtime", service=object()),
    lambda: PluginSpec(name="plugin"),
    lambda: ServiceSpec(name="service", service=object()),
    lambda: WebSocketEndpoint(path="/events", handler=lambda: None),
    lambda: EndpointContext(request=Request({"type": "http"}), principal=Principal(id="caller")),
    lambda: ClientSurface(name="ui", source=".", build=("true",), artifact="dist", routes=("/ui",)),
    lambda: AgentApplication(name="app", version="1", primary_agent=AgentSpec(name="main", session=SessionPolicy.durable())),
    lambda: AgentApplication(name="app", version="1", primary_agent=AgentSpec(name="main", session=SessionPolicy.durable())).compile(),
])
def test_runtime_declarations_are_frozen_validated_mappings(make):
    model = make()
    assert isinstance(model, modict)
    key = next(iter(model))
    with pytest.raises(TypeError):
        model[key] = model[key]
    with pytest.raises(KeyError):
        type(model)({**model, "misspelled_option": True})


def test_declaration_reconstruction_preserves_live_references_and_validates_together():
    service = object()
    callback = lambda: service
    original = Extension(name="runtime", service_factory=callback)
    resolved = Extension({**original, "service": service, "service_factory": None})
    assert original.service is None
    assert original.service_factory is callback
    assert resolved.service is service
    assert resolved.service_factory is None
    with pytest.raises(ValueError, match="mutually exclusive"):
        Extension({**original, "service": service})


def test_agent_recipe_keeps_runtime_handles_and_snapshots_sequence_inputs():
    plugin, initializer, projection = object(), object(), object()
    plugins = [plugin]
    spec = AgentSpec(name="main", plugins=plugins, initializers=[initializer])
    plugins.clear()
    assert spec.plugins == (plugin,)
    assert spec.plugins[0] is plugin
    assert spec.initializers[0] is initializer
    changed = spec.with_plugins(projection)
    assert changed.initializers[0] is initializer
    assert changed.plugins == (plugin, projection)
    assert spec.plugins == (plugin,)


def test_transient_records_keep_objects_and_private_authority_out_of_public_lease():
    async def scenario():
        future = asyncio.get_running_loop().create_future()
        queue = asyncio.Queue()
        socket = object()
        steering = PendingSteering(prompt="hello", future=future)
        connection = ClientApplicationConnection(client_id="client", websocket=socket, outgoing=queue)
        assert steering.future is future
        assert connection.websocket is socket
        assert connection.outgoing is queue
        connection.revoked = True
        assert connection.revoked is True

    asyncio.run(scenario())
    now = datetime.now(timezone.utc)
    pairing = PendingPairing(id="pair", secret_hash="hash", endpoint="https://example.test",
                             created_at=now, expires_at=now)
    pairing.failed_attempts += 1
    assert pairing.failed_attempts == 1
    lease = CapabilityLease(lease_id="lease", capability="media_runtime", client_id="client",
                            connection_id="private", granted_at=now, expires_at=now, generation=1)
    assert lease.granted_at is now
    assert "connection_id" not in lease.public()
    assert isinstance(lease.public()["granted_at"], str)


def test_browser_handles_are_attributes_not_configuration(tmp_path):
    first = BrowserSession(profile_dir=tmp_path / "first")
    second = BrowserSession(profile_dir=tmp_path / "second")
    context = object()
    first._context = modict.attr(context)
    assert first.context is context
    assert first.started and not second.started
    assert "_context" not in first
    assert "_cleanup_tasks" not in first
    assert first._cleanup_tasks is not second._cleanup_tasks
    assert first.extra_args is not second.extra_args
    assert first.viewport is not second.viewport
