"""Explicitly opted-in backend inference smoke test with isolated local data."""

import os
import time

import pytest
from fastapi.testclient import TestClient

from harness_core.agent.runtime.protocol import PromptRequest
from starter.server import create_app

pytestmark = pytest.mark.skipif(
    os.environ.get("STARTER_LIVE_TESTS") != "1",
    reason="makes real backend requests using account quota",
)


def test_starter_real_inference_with_tool_and_canonical_response(tmp_path):
    app = create_app(tmp_path, token="isolated-test", origin="http://testserver")
    events = []

    async def observe(output):
        if output.get("type") == "command_event":
            events.append(output.event)

    app.state.runtime.register_observer(observe)
    with TestClient(app) as client:
        command = PromptRequest(id="tool-smoke", prompt=(
            "Use the bash tool once to execute: printf 'starter-tool-ok'. "
            "Do not change files. Then reply with that exact command output."
        ))
        client.portal.call(app.state.runtime.command, command, 60)
        page = client.get("/api/v1/session", headers={"authorization": "Bearer isolated-test"}).json()
        text = "\n".join(part.get("text", "") for turn in page["turns"]
                         for item in turn["items"] if item.get("role") == "assistant"
                         for part in item.get("content", []))
        assert "starter-tool-ok" in text
        assert any(event.type == "agent.tool_call.start" for event in events)
        assert any(event.type == "agent.tool_call.end" for event in events)


def test_starter_real_memory_curates_indexes_and_survives_reconstruction(tmp_path):
    from harness_core.plugins.memory.store import MemoryStore

    app = create_app(tmp_path, token="isolated-test", origin="http://testserver")
    results = []
    app.state.runtime.register_fleet_result_handler(lambda result: results.append(result))
    with TestClient(app) as client:
        client.portal.call(app.state.runtime.command, PromptRequest(
            id="memory-smoke",
            prompt=("My project is called Copper Finch "
                    "and its source language is Rust. Please use memory.remember to preserve "
                    "both facts in durable memory, then briefly acknowledge."),
        ), 90)
        store = MemoryStore(tmp_path / "relational-memory.sqlite3")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if results:
                break
            status = app.state.runtime.fleet.status()
            assert not any(worker["error"] for worker in status), status
            time.sleep(0.5)
        assert results and results[-1].status == "completed", results or app.state.runtime.fleet.status()
        entries = store.active()
        assert entries, results
        assert all(entry.embedding for entry in entries), app.state.runtime.fleet.status()
        content = " ".join(entry.content for entry in entries).lower()
        assert "copper finch" in content and "rust" in content

    # A reconstructed agent retrieves the persisted corpus through real embeddings.
    # backend. Query tools directly to avoid relying on model tool-choice luck.
    from starter.application import application

    session = tmp_path / "session.json"
    agent = application.compile().primary_agent.build_agent(session)
    recalled = agent.plugin("memory").search("What language does Copper Finch use?")
    assert recalled["status"] == "found"
    assert any("rust" in entry["content"].lower() for entry in recalled["memories"])
