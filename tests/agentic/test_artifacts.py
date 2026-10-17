import asyncio
import json

import pytest

from the_framework.agent import (
    Agent, Artifact, Image, ResponseItem, Session, Worker,
)
from the_framework.agent.models.responses import CompactionSummary, FunctionCall
from the_framework.plugins.realtime import RealtimeExtension


def test_artifact_roundtrip_projection_and_compaction(tmp_path):
    path = tmp_path / "session.json"
    agent = Agent(session_path=path, client=object())
    agent.add_message("user", "Montre-moi le résultat")
    artifact = Artifact(kind="widget", payload={"widget": "weather", "temperature": 18})
    agent.add_response_item(artifact)
    restored = Session.open(path)
    assert isinstance(restored.history[-1], Artifact)
    assert json.loads(json.dumps(ResponseItem.from_dict(json.loads(json.dumps(artifact))))) == json.loads(json.dumps(artifact))
    projected = restored.to_api_format(include_images=False)
    assert projected[-1]["role"] == "developer"
    assert '"temperature":18' in projected[-1]["content"][0]["text"]
    assert "not a client rendering acknowledgement" in projected[-1]["content"][0]["text"]
    assert not any(item.get("type") == "artifact" for item in projected)
    restored.commit_compaction(
        [CompactionSummary(encrypted_content="summary")], anchor_id="anchor", archive_anchor_limit=10,
    )
    restarted = Session.open(path)
    assert [item.id for item in Worker.conversation_projection(restarted.archive)] == [
        agent.session.history[0].id, artifact.id,
    ]
    assert restarted.append(artifact) is None  # A provider cannot republish it after compaction.


@pytest.mark.parametrize("kwargs", [
    {"kind": ""}, {"kind": "   "}, {}, {"kind": "x", "payload": []},
    {"kind": "x", "payload": {"value": float("nan")}},
])
def test_artifact_requires_kind_and_json_object(kwargs):
    with pytest.raises((ValueError, TypeError, KeyError)):
        Artifact(**kwargs)


def test_tool_and_command_return_native_artifacts_without_display_call(tmp_path):
    agent = Agent(client=object())
    artifact = Artifact(kind="file", payload={"src": "/api/v1/artifacts/report.txt"})
    image = tmp_path / "vision.png"
    image.write_bytes(b"vision")

    @agent.add_tool
    def generate():
        """Return vision and presentation independently."""
        return {"generated": True}, Image(path=str(image)), artifact

    output = asyncio.run(agent.agentic_loop.run_tool_call(FunctionCall(
        name="generate", arguments='{"user_feedback":"Je prépare le résultat"}', call_id="call",
    )))
    assert artifact in agent.session.history
    assert artifact.call_id == "call"
    assert artifact.id in json.loads(output.output)["message_ids"]
    assert Worker.conversation_projection(agent.session.history) == [artifact]

    @agent.add_command
    def show():
        """Return an explicit widget."""
        return Artifact(kind="widget", payload={"count": 3})

    outputs = agent.agentic_loop.command_outputs("/show")
    assert isinstance(outputs[0], Artifact)
    assert outputs[0].command_id


def test_provider_artifacts_are_ephemeral_events_not_history(tmp_path):
    agent = Agent(session_path=tmp_path / "session.json", client=object())
    artifact = Artifact(id="browser-view", kind="image", payload={"src": "/screenshot.png"})
    events = []
    agent.events.subscribe("agent.provider_artifacts.updated", events.append)

    @agent.providers.add
    async def browser():
        return {"context": "ephemeral"}, artifact

    agent.agentic_loop.generation = lambda payload: None
    agent.context.needs_compaction = lambda payload: False
    for _ in range(2):
        asyncio.run(agent.agentic_loop.step())
        projected = asyncio.run(agent.context.ainput())
        assert "current turn only" in projected[-1]["content"][0]["text"]
        assert "not stored" in projected[-1]["content"][0]["text"]
    assert agent.session.history == agent.session.archive == []
    assert not agent.session.path.exists()
    assert len(events) == 2
    assert [item.id for item in events[-1]["items"]] == [artifact.id]
    agent.providers.clear()
    asyncio.run(agent.agentic_loop.step())
    assert events[-1]["items"] == []


def test_durable_artifacts_are_in_voice_initial_context_but_providers_are_not():
    agent = Agent(client=object())
    artifact = Artifact(id="tool-artifact", kind="widget", payload={"value": 42})
    provider_artifact = Artifact(id="provider-artifact", kind="widget", payload={"value": 21})
    agent.add_response_item(artifact)

    @agent.providers.add(channels=["realtime"])
    def dashboard():
        return "dashboard context", provider_artifact

    voice = agent.add_extension(RealtimeExtension)
    voice.config.provider_whitelist = ["dashboard"]
    snapshot = voice.session_snapshot({"items": list(agent.session.history), "extensions": {}})
    assert agent.session.history == [artifact]
    initial = snapshot["extensions"]["realtime"]["session"]["initial_items"]
    assert initial == [artifact.to_api_format()]


def test_saved_session_contains_references_not_image_bytes(tmp_path):
    import base64
    from PIL import Image as PILImage
    from the_framework.plugins.artifacts.server import ArtifactAssets
    image = tmp_path / "source.png"
    PILImage.new("RGB", (20, 10), "teal").save(image)
    encoded = base64.b64encode(image.read_bytes()).decode()
    session = Session.open(tmp_path / "session.json")
    session.append(Image(path=str(image), description="Vision only"))
    payload = ArtifactAssets(tmp_path / "assets").publish("image", {"path": str(image)})
    artifact = Artifact(kind="image", payload=payload)
    session.append(artifact)
    on_disk = session.path.read_text()
    assert encoded not in on_disk and "base64," not in on_disk
    # Encoding for the remote vision API is transient and never written back.
    assert encoded in json.dumps(session.to_api_format(max_input_images=1))
    session.save()
    assert encoded not in session.path.read_text()
    image.unlink()
    from urllib.parse import unquote
    copied = tmp_path / "assets" / unquote(payload["src"].rsplit("/", 1)[-1])
    assert copied.is_file()
    copied.unlink()
    assert any(item.id == artifact.id for item in Session.open(session.path).archive)
