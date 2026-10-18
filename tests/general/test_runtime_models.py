"""Declarations are immutable snapshots; public leases omit private authority."""

from datetime import datetime, timezone

import pytest
from fastapi import Request

from the_framework.agent import AgentSpec
from the_framework.server.clients.remote import CapabilityLease
from the_framework.server.composition.application import Application, ServerExtension, Plugin
from the_framework.server.api.endpoints import EndpointContext, Principal
from the_framework.server.clients.surfaces import ClientSurface
from the_framework.server.api.websockets import WebSocketEndpoint


@pytest.mark.parametrize("make", [
    lambda: AgentSpec(name="main"),
    lambda: ServerExtension(name="runtime", service=object()),
    lambda: Plugin(name="plugin"),
    lambda: WebSocketEndpoint(path="/events", handler=lambda: None),
    lambda: EndpointContext(request=Request({"type": "http"}), principal=Principal(id="caller")),
    lambda: ClientSurface(name="ui", source=".", build=("true",), artifact="dist", routes=("/ui",)),
    lambda: Application(name="app", version="1", agents=(AgentSpec(name="main"),)),
    lambda: Application(name="app", version="1", agents=(AgentSpec(name="main"),)).compile(),
])
def test_runtime_declarations_are_frozen_validated_mappings(make):
    model = make()
    key = next(iter(model))
    with pytest.raises(TypeError):
        model[key] = model[key]
    with pytest.raises(KeyError):
        type(model)({**model, "misspelled_option": True})


def test_declaration_reconstruction_preserves_live_references_and_validates_together():
    service = object()
    callback = lambda: service
    original = ServerExtension(name="runtime", service_factory=callback)
    resolved = ServerExtension({**original, "service": service, "service_factory": None})
    assert original.service is None
    assert original.service_factory is callback
    assert resolved.service is service
    assert resolved.service_factory is None
    with pytest.raises(ValueError, match="mutually exclusive"):
        ServerExtension({**original, "service": service})


def test_agent_recipe_keeps_runtime_handles_and_snapshots_sequence_inputs():
    plugin, initializer, projection = object(), object(), object()
    plugins = [plugin]
    spec = AgentSpec(name="main", extensions=plugins, initializers=[initializer])
    plugins.clear()
    assert spec.extensions == (plugin,)
    assert spec.extensions[0] is plugin
    assert spec.initializers[0] is initializer
    changed = spec.with_extensions(projection)
    assert changed.initializers[0] is initializer
    assert changed.extensions == (plugin, projection)
    assert spec.extensions == (plugin,)


def test_public_lease_omits_private_connection_authority_and_serializes_dates():
    now = datetime.now(timezone.utc)
    lease = CapabilityLease(lease_id="lease", capability="media_runtime", client_id="client",
                            connection_id="private", granted_at=now, expires_at=now, generation=1)
    assert "connection_id" not in lease.public()
    assert isinstance(lease.public()["granted_at"], str)
