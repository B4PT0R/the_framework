import asyncio
import json

import pytest

from the_framework.agent import Agent
from the_framework.agent.context.session import Session
from the_framework.agent.models.usage import CacheHitStats, ResponseUsage
from the_framework.agent.runtime.protocol import StatusRequest, WorkerOutput
from the_framework.agent.runtime.worker import Worker


def usage(cached=80, tokens=100):
    return ResponseUsage(input_tokens=tokens, output_tokens=10, total_tokens=tokens + 10,
                         input_tokens_details={"cached_tokens": cached})


def test_ema_uses_reported_input_ratio_once_per_call():
    stats = CacheHitStats()
    first = stats.observe(usage())
    assert stats.samples == 0  # immutable update for atomic persistence/rollback
    assert first.ema_percent == first.last_percent == 80
    second = first.observe(usage(0, 1000))
    assert second.ema_percent == pytest.approx(72)
    assert second.last_percent == 0
    assert second.samples == 2
    assert second.updated_at >= first.updated_at
    # Different input sizes: each measured call contributes one percentage.
    assert second.observe(usage(100)).ema_percent == pytest.approx(74.8)


@pytest.mark.parametrize("observation", [None, usage(None), usage(0, 0), usage(-1), usage(101),
    ResponseUsage(input_tokens=100, output_tokens=1, total_tokens=101)])
def test_unreported_or_invalid_usage_is_not_a_zero_hit(observation):
    first = CacheHitStats().observe(usage())
    missing = first.observe(observation)
    assert missing.ema_percent == 80
    assert missing.samples == 1
    assert missing.unreported == 1
    assert missing.last_percent is None
    assert missing.updated_at == first.updated_at
    assert CacheHitStats().observe(observation).ema_percent is None


def test_session_round_trip_and_failed_save_are_atomic(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    session = Session.open(path)
    session.set_context_usage(usage())
    restored = Session.open(path)
    assert isinstance(restored.cache_hit, CacheHitStats)
    restored.set_context_usage(usage(0))
    assert restored.cache_hit.ema_percent == pytest.approx(72)
    before = restored.cache_hit.to_dict()

    def fail(self):
        raise OSError("write failed")

    monkeypatch.setattr(Session, "save", fail)
    with pytest.raises(OSError):
        restored.set_context_usage(usage(100))
    assert restored.cache_hit.to_dict() == before
    assert restored.context_usage.input_tokens_details.cached_tokens == 0
    assert Session.open(path).cache_hit.to_dict() == before


def test_worker_status_and_missing_response_usage(tmp_path):
    async def run():
        agent = Agent(client=object())
        agent.session = Session.open(tmp_path / "session.json")
        agent.context.record_response_usage({"usage": usage(0).to_dict()})
        agent.context.record_response_usage({})
        outgoing = []

        async def send(message):
            outgoing.append(message)

        worker = Worker(agent, asyncio.Queue().get, send)
        await worker.handle(StatusRequest(id="status"))
        status = WorkerOutput.from_dict(json.loads(json.dumps(outgoing[-1])))
        assert status.cache_hit.ema_percent == 0
        assert status.cache_hit.samples == 1
        assert status.cache_hit.unreported == 1
        assert status.cache_hit.last_percent is None
        assert Session.open(agent.session.path).cache_hit == status.cache_hit

    asyncio.run(run())
