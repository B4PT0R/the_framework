import asyncio
import json
from types import SimpleNamespace

import pytest

from the_framework.server.clients.remote import ClientRemoteService, MAX_NOTIFICATION_EVENTS


def service(tmp_path):
    return ClientRemoteService(SimpleNamespace(), tmp_path / "clients.json")


def test_notification_journal_survives_restart_and_drains_shutdown(tmp_path):
    async def run():
        first = service(tmp_path)
        initial = await first.wait_for_notification()
        first._schedule_notifications("one")
        first._schedule_notifications("one")
        first._schedule_notifications("alarm", kind="alarm")
        await first.stop()
        second = service(tmp_path)
        assert first.instance_id != second.instance_id
        resumed = await second.wait_for_notification(after=0, server_instance_id=initial["server_instance_id"])
        assert resumed["status"] == "events"
        assert [event["message_id"] for event in resumed["events"]] == ["one", "alarm"]
        assert resumed["cursor"] == 2
        second._schedule_notifications("one")
        await second.stop()
        assert second.notification_sequence == 2
        stored = json.loads(second.notifications_path.read_text())
        assert set(stored["events"][0]) == {"sequence", "server_instance_id", "message_id", "kind", "emitted_at"}
        assert second.notifications_path.stat().st_mode & 0o777 == 0o600
    asyncio.run(run())


def test_expired_or_foreign_cursor_is_explicit_reset(tmp_path):
    async def run():
        remote = service(tmp_path)
        # Seed the bounded store without hundreds of fsyncs.
        remote.notification_sequence = MAX_NOTIFICATION_EVENTS + 1
        remote.notification_journal.append({"sequence": 2})
        for after, instance in [(0, remote.notification_instance_id), (999, remote.notification_instance_id), (2, "foreign")]:
            result = await remote.wait_for_notification(after=after, server_instance_id=instance)
            assert result["status"] == "reset" and result["events"] == []
    asyncio.run(run())


def test_persistence_failure_never_advances_visible_cursor(tmp_path, monkeypatch):
    async def run():
        remote = service(tmp_path)
        def fail(*args):
            raise OSError("disk unavailable")
        monkeypatch.setattr("the_framework.server.clients.remote._atomic_json", fail)
        with pytest.raises(OSError):
            await remote._record_notification("one")
        assert remote.notification_sequence == 0 and not remote.notification_journal
    asyncio.run(run())
