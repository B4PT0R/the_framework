"""Alarm audio remains a resource reference, not executable policy or external retrieval."""
import asyncio

import pytest

from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.scheduler.waits import WaitBook, alarm_audio, date

NOW = date("2026-10-25T10:00:00Z")
SRC = "/api/v1/bucket/voice.wav"


@pytest.mark.parametrize("src", ["https://example.com/a.wav", "//host/a.wav", "/a/../b", "/a/%2e.wav", "/a.wav?secret=x", "/a\\b", "/a/#b", "", 1, "/.."])
def test_audio_reference_is_plain_same_origin(src):
    with pytest.raises(ValueError):
        alarm_audio(src, "alarm")


def test_audio_is_alarm_only_and_roundtrips_in_waits():
    book = WaitBook()
    payload = {"name": "Speech", "context": "Private", "timeout_seconds": 60, "response_kind": "ack",
               "alert": {"at": "2026-10-25T11:00:00Z", "kind": "alarm", "title": "Wake", "audio_src": SRC}}
    book.create("speech", payload, NOW)
    restored = WaitBook(book.snapshot())
    assert restored.alerts(NOW)[0]["audio_src"] == SRC
    for event in ("audio_ready", "audio_unavailable"):
        assert restored.receipt("speech", "phone", event, NOW)["status"] == "waiting"
    payload["alert"]["kind"] = "message"
    with pytest.raises(ValueError, match="audio_src"):
        WaitBook().create("silent", payload, NOW)


def test_wake_audio_projection_update_persistence_and_atomic_validation(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: NOW)
        service = SchedulerService(tmp_path / "scheduler.json", None)
        await service.create(wake_id="w", name="Wake", prompt="Private", at="2026-10-25T11:00:00Z",
                             delivery="alarm", audio_src=SRC)
        assert (await service.native_alerts())["alerts"][0]["audio_src"] == SRC
        restored = SchedulerService(service.path, None)
        restored.wakes = restored._load()
        assert restored.wakes["w"].audio_src == SRC
        for changes in ({"delivery": "message"}, {"name": "Changed", "audio_src": "https://bad/a.wav"}, {"audio_src": False}, {"audio_src": 0}):
            with pytest.raises(ValueError):
                await service.update(wake_id="w", **changes)
            assert service.wakes["w"].name == "Wake" and service.wakes["w"].audio_src == SRC
        await service.update(wake_id="w", audio_src="")
        assert "audio_src" not in (await service.native_alerts())["alerts"][0]
        await service.update(wake_id="w", delivery="message")
        with pytest.raises(ValueError):
            await service.create(wake_id="bad", name="Bad", prompt="Private", at="2026-10-25T11:00:00Z", audio_src=SRC)
    asyncio.run(run())
