"""Agent-owned context telemetry and safe, explicit canonical compaction."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from the_framework.agent import Agent
from the_framework.agent.models.responses import FunctionCall, FunctionCallOutput
from the_framework.plugins.agentic import AgenticExtension


def make_agent(tmp_path):
    agent = Agent(client=object(), session_path=tmp_path / "session.json",
                  compaction={"context_token_limit": 100_000, "trigger_ratio": 0.9})
    plugin = agent.add_extension(AgenticExtension)
    return agent, plugin


def test_occupancy_is_ephemeral_text_only_and_does_not_sample_or_compact(tmp_path, monkeypatch):
    agent, plugin = make_agent(tmp_path)
    provider = agent.registry("providers")["context_occupancy"]
    assert provider.channels == ["text"]
    assert agent.registry("tools")["agentic"].find("compact") is not None
    before = agent.session.path.read_bytes() if agent.session.path.exists() else None

    def forbidden(*_args, **_kwargs):
        pytest.fail("telemetry must not build another payload, sample providers or compact")

    monkeypatch.setattr(agent.agentic_loop, "generation_payload", forbidden)
    monkeypatch.setattr(agent, "compact", forbidden)
    monkeypatch.setattr(type(agent.providers), "aoutputs", forbidden)
    monkeypatch.setattr(type(agent.providers), "outputs", forbidden)
    assert plugin.context_occupancy()["status"] == "unavailable"
    assert plugin.context_occupancy()["occupied_percent"] is None
    for tokens, percent in [(25_000, 25.0), (110_000, 110.0)]:
        agent.context.last_local_input_tokens = tokens
        assert plugin.context_occupancy() == {
            "status": "estimated", "source": "last_prepared_request_local_estimate",
            "input_tokens": tokens, "context_token_limit": 100_000,
            "occupied_percent": percent, "automatic_compaction_percent": 90.0,
            "compaction_requested": False,
        }
    assert not agent.session.history and not agent.session.archive
    after = agent.session.path.read_bytes() if agent.session.path.exists() else None
    assert before == after


def test_occupancy_handles_unavailable_budget(tmp_path):
    agent, plugin = make_agent(tmp_path)
    agent.context.last_local_input_tokens = 100
    agent.configs.compaction.context_token_limit = 0
    assert plugin.context_occupancy()["occupied_percent"] is None
    assert plugin.context_occupancy()["status"] == "unavailable"


def test_requested_compaction_waits_for_all_tool_results_and_resumes_same_turn(tmp_path, monkeypatch):
    agent, plugin = make_agent(tmp_path)
    loop = agent.agentic_loop
    order = []
    requests = []
    samples = []

    @agent.providers.add(channels=["text"])
    def sample_once():
        samples.append(len(samples) + 1)
        return {"sample": samples[-1]}

    @agent.tools.add
    def finish_batch():
        """Record another tool in this batch."""
        assert loop.compaction_requested
        order.append("second_tool")
        return {"finished": True}

    def compact(**payload):
        order.append("compact")
        requests.append(payload)
        assert loop.compaction_requested
        outputs = [item for item in payload["input"] if item["type"] == "function_call_output"]
        assert [item["call_id"] for item in outputs] == ["compact-1", "compact-2", "finish"]
        return {"id": "compacted", "output": [
            {"type": "compaction_summary", "encrypted_content": "anchor"}],
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}

    agent.client = SimpleNamespace(responses=SimpleNamespace(compact=compact))
    generations = []

    def generation(payload):
        generations.append(payload)
        loop.request_next_step = False
        loop.tool_calls = []
        if len(generations) == 1:
            calls = [
                FunctionCall(name="compact", namespace="agentic", call_id="compact-1",
                             arguments=json.dumps({"user_feedback": "Prepare fresh context"})),
                FunctionCall(name="compact", namespace="agentic", call_id="compact-2",
                             arguments=json.dumps({"user_feedback": "Coalesce the same request"})),
                FunctionCall(name="finish_batch", call_id="finish",
                             arguments=json.dumps({"user_feedback": "Finish the current batch"})),
            ]
            for call in calls:
                agent.add_response_item(call)
            loop.tool_calls = calls
        else:
            order.append("resumed_generation")
            assert not loop.compaction_requested
            assert payload["input"][0]["type"] == "compaction_summary"

    monkeypatch.setattr(loop, "generation", generation)
    asyncio.run(loop.turn("A substantially different task"))

    assert order == ["second_tool", "compact", "resumed_generation"]
    assert len(requests) == 1 and len(generations) == 2
    assert samples == [1, 2]  # No resampling during compaction or telemetry.
    assert not loop.compaction_requested
    assert any(isinstance(item, FunctionCallOutput) for item in agent.session.archive)
    assert not any(item.type == "provider_output" for item in agent.session.archive)


def test_failed_compaction_is_not_acknowledged_as_completed_and_keeps_request(tmp_path, monkeypatch):
    agent, plugin = make_agent(tmp_path)
    loop = agent.agentic_loop
    history = list(agent.session.history)
    assert plugin.compact() == {"status": "compaction_requested", "when": "before_next_model_step"}
    assert plugin.context_occupancy()["compaction_requested"]

    def fail():
        raise RuntimeError("compaction backend unavailable")

    monkeypatch.setattr(agent, "compact", fail)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        asyncio.run(loop.step())
    assert loop.compaction_requested
    assert list(agent.session.history) == history


def test_request_survives_graceful_turn_boundary_until_next_step(tmp_path, monkeypatch):
    agent, plugin = make_agent(tmp_path)
    loop = agent.agentic_loop
    order = []

    async def finish():
        plugin.compact()
        agent.end_turn()
        loop.request_next_step = True

    monkeypatch.setattr(loop, "step", finish)
    asyncio.run(loop.turn())
    assert loop.compaction_requested
    monkeypatch.undo()
    monkeypatch.setattr(agent, "compact", lambda: order.append("compact"))
    monkeypatch.setattr(loop, "generation", lambda _payload: order.append("generation"))
    asyncio.run(loop.turn())
    assert order == ["compact", "generation"]
    assert not loop.compaction_requested
