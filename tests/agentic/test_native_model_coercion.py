from copy import deepcopy
import json
import pytest

from the_framework.agent.extensions.schema import Parameters, Param, Properties
from the_framework.agent.runtime.protocol import WorkerOutput, WorkerReady
from the_framework.agent.runtime.protocol import CommandEvent, CommandAccepted, CommandCompleted, CommandFailed, PromptRequest
from the_framework.agent.runtime.protocol import SessionSnapshot, SessionPage, SessionTurn
from the_framework.agent.runtime.protocol import WorkerStatus
from the_framework.agent.models.lifecycle import AgentCompactionEnd
from the_framework.agent.models.responses import Compaction
from the_framework.agent.context.session import SessionWatermark
from the_framework.agent.models.events import ResponseOutputItemAdded
from the_framework.agent.models.responses import Message
from the_framework.agent.models.worker import WorkerProfile
from the_framework.agent.models.usage import ResponseUsage, TokenDetails
from the_framework.server.clients.surfaces import SurfaceRelease
from the_framework.server.composition.application import BuildContext, Capability, CapabilityRequirement
from the_framework.server.clients.remote import RemoteClientPolicy
from the_framework.server.api.endpoints import Principal


def test_ready_reconstructs_nested_profiles_without_mutating_input():
    payload = {"type": "worker_ready", "session_id": "session", "specialists": [
        {"name": "example", "instructions": "Example task"},
    ]}
    original = deepcopy(payload)
    result = WorkerOutput.from_dict(payload)
    assert isinstance(result, WorkerReady)
    assert isinstance(result.specialists[0], WorkerProfile)
    assert payload == original
    assert type(payload["specialists"][0]) is dict


def test_parameter_models_reconstruct_their_declared_field_types():
    payload = {"properties": {"query": {"type": "string", "description": "Search"}}}
    result = Parameters.from_dict(payload)
    assert isinstance(result.properties, Properties)
    assert isinstance(result.properties.query, Param)
    assert result.properties.query.type == "string"
    assert result.required == []


def test_surface_result_is_exactly_its_public_payload():
    result = SurfaceRelease(surface="main", release="current", previous="old")
    assert json.loads(json.dumps(result)) == {
        "surface": "main", "release": "current", "previous": "old",
    }
    with pytest.raises(TypeError):
        result.release = "changed"


def test_build_context_is_a_frozen_shallow_mapping_of_runtime_objects():
    service = object()
    settings = {"nested": {"value": 1}}
    source = {"service": service, "settings": settings}
    context = BuildContext(source)
    source.clear()
    assert context.require("service") is service
    assert context.get("settings") is settings
    assert context["settings"]["nested"] is settings["nested"]
    with pytest.raises(TypeError):
        context["service"] = object()
    with pytest.raises(KeyError, match="missing build context value"):
        context.require("missing")


def test_build_context_accepts_ordered_exports_without_mutating_inputs():
    initial = object()
    exported = object()
    context = BuildContext({"initial": initial})
    assert context.provide("prepared", exported) is exported
    assert context.get("prepared") is exported
    assert context.require("prepared") is exported
    assert context.require("initial") is initial
    assert dict(context) == {"initial": initial}
    with pytest.raises(ValueError, match="duplicate build context value"):
        context.provide("prepared", object())
    with pytest.raises(ValueError, match="duplicate build context value"):
        context.provide("initial", object())
    with pytest.raises(ValueError, match="nonempty string"):
        context.provide("", object())

    first, second = object(), object()
    context.contribute("feature.policy", first)
    context.contribute("feature.policy", second)
    assert context.contributions("feature.policy") == (first, second)
    assert context.contributions("missing") == ()
    with pytest.raises(ValueError, match="contribution name"):
        context.contribute("", object())


@pytest.mark.parametrize("model, version", [
    (Capability, "version"), (CapabilityRequirement, "min_version"),
])
def test_capability_contracts_validate_and_serialize(model, version):
    contract = model(name="example.read", **{version: 2})
    assert json.loads(json.dumps(contract)) == {"name": "example.read", version: 2}
    with pytest.raises(ValueError):
        model(name="unnamespaced")
    with pytest.raises(ValueError):
        model(name="example.read", **{version: 0})
    with pytest.raises(TypeError):
        contract.name = "other.read"


@pytest.mark.parametrize("adapter", ["mapping", "to_dict", "model_dump"])
def test_usage_keeps_sdk_adapters_and_reconstructs_token_details(adapter):
    payload = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
               "input_tokens_details": {"cached_tokens": 3},
               "output_tokens_details": None}
    source = payload if adapter == "mapping" else type(
        "Usage", (), {adapter: lambda self: payload},
    )()
    usage = ResponseUsage.from_dict(source)
    assert isinstance(usage.input_tokens_details, TokenDetails)
    assert usage.input_tokens_details.cached_tokens == 3
    assert usage.output_tokens_details is None
    assert type(payload["input_tokens_details"]) is dict
    assert ResponseUsage.from_dict(usage) is usage


@pytest.mark.parametrize("construct", [CommandEvent, WorkerOutput.from_dict])
def test_event_polymorphism_is_reconstructed_without_mutating_wire_payload(construct):
    payload = {"type": "command_event", "command_id": "turn", "event": {
        "type": "response.output_item.added", "item": {
            "type": "message", "role": "user", "content": [],
        },
    }}
    original = deepcopy(payload)
    event = construct(payload)
    assert isinstance(event.event, ResponseOutputItemAdded)
    assert isinstance(event.event.item, Message)
    assert payload == original
    assert type(payload["event"]) is dict
    assert type(payload["event"]["item"]) is dict
    assert CommandEvent(command_id="other", event=event.event).event is event.event
    assert ResponseOutputItemAdded(item=event.event.item).item is event.event.item


@pytest.mark.parametrize("model", [CommandAccepted, CommandCompleted, CommandFailed])
def test_command_envelopes_reconstruct_without_mutating_request(model):
    payload = {"command": {"type": "prompt_request", "id": "turn", "prompt": "hello",
                           "input_items": [{"type": "message", "role": "user", "content": []}]}}
    if model is CommandFailed:
        payload["error"] = "failed"
    original = deepcopy(payload)
    envelope = model(payload)
    assert isinstance(envelope.command, PromptRequest)
    assert isinstance(envelope.command.input_items[0], Message)
    assert payload == original
    assert type(payload["command"]["input_items"][0]) is dict
    assert model(command=envelope.command).command is envelope.command


@pytest.mark.parametrize("model", [SessionSnapshot, SessionPage])
def test_session_projections_reconstruct_without_mutating_input(model):
    item = {"type": "message", "role": "user", "content": []}
    payload = {"watermark": {"session_id": "s", "revision": 1, "anchor_id": None, "tail_start": 0}}
    if model is SessionSnapshot:
        payload.update(items=[item], archive=[item])
    else:
        payload["turns"] = [{"id": "t", "items": [item]}]
    original = deepcopy(payload)
    result = model(payload)
    assert isinstance(result.watermark, SessionWatermark)
    if model is SessionSnapshot:
        assert isinstance(result["items"][0], Message)
        assert isinstance(result.archive[0], Message)
    else:
        assert isinstance(result.turns[0], SessionTurn)
        assert isinstance(result.turns[0]["items"][0], Message)
    assert payload == original
    assert type(item) is dict
    if model is SessionSnapshot:
        assert type(payload["items"][0]) is dict
    else:
        assert type(payload["turns"][0]["items"][0]) is dict


def test_remote_policy_keeps_set_semantics_and_validates_declared_types():
    policy = RemoteClientPolicy(capabilities=frozenset({"display"}))
    assert isinstance(policy.capabilities, frozenset)
    assert RemoteClientPolicy(policy) == policy
    with pytest.raises(TypeError):
        policy.capabilities = frozenset()
    with pytest.raises(KeyError):
        RemoteClientPolicy(capabilites=frozenset({"display"}))
    with pytest.raises(TypeError):
        RemoteClientPolicy(default_scopes="read")


def test_principal_is_frozen_and_has_independent_attributes():
    first, second = Principal(id="first"), Principal(id="second")
    first.attributes["context"] = "local"
    assert second.attributes == {}
    assert first.scopes == frozenset()
    with pytest.raises(TypeError):
        first.scopes = frozenset({"*"})
    with pytest.raises(TypeError):
        Principal(id="caller", scopes="admin")


def test_worker_status_reconstructs_both_active_requests_without_mutation():
    request = {"type": "prompt_request", "id": "task", "prompt": "test"}
    payload = {"active": request, "foreground_active": request}
    original = deepcopy(payload)
    status = WorkerStatus(payload)
    assert isinstance(status.active, PromptRequest)
    assert isinstance(status.foreground_active, PromptRequest)
    assert payload == original
    assert type(payload["active"]) is dict
    assert WorkerStatus(active=status.active).active is status.active
    assert WorkerStatus().active is None


def test_retired_compaction_decode_does_not_remove_source_metadata():
    item = {"type": "compaction", "encrypted_content": "opaque", "created_by": "source"}
    event = AgentCompactionEnd(retired_items=[item])
    assert isinstance(event.retired_items[0], Compaction)
    assert item["created_by"] == "source"
