import asyncio
from datetime import timedelta

import pytest

from the_framework.agent.runtime.protocol import CommandAccepted, CommandCompleted, CommandFailed, PromptRequest
from the_framework.plugins.scheduler.service import SchedulerService
from the_framework.plugins.scheduler.waits import WaitBook, date, stamp

NOW = date("2026-10-20T10:00:00+02:00")


def create(book=None, **changes):
    book = book or WaitBook()
    item = book.create("w", {"name": "Confirmation", "context": "No consent from silence", "timeout_seconds": 60, **changes}, NOW)
    return book, item


def alert(**changes):
    return {"response_kind": "ack", "alert": {"at": stamp(NOW + timedelta(minutes=5)), "kind": "message", "title": "Reminder"}, **changes}


def test_reply_resolves_without_duplicate_turn_and_snapshot_is_detached():
    book, item = create()
    item["name"] = "Changed"
    assert book.get("w")["name"] == "Confirmation"
    with pytest.raises(ValueError, match="only one"):
        book.create("other", {"name": "x", "context": "x", "timeout_seconds": 30}, NOW)
    result = book.reply("Maybe", NOW + timedelta(seconds=30))
    assert result["status"] == "answered" and result["response"] == "Maybe"
    assert book.due(NOW + timedelta(hours=1)) == []
    assert book.reply("Again", NOW) is None


def test_timeout_boundary_late_response_and_persistence():
    book, _ = create()
    result = book.reply("Late", NOW + timedelta(seconds=60))
    assert result["status"] == "timed_out"
    assert result["late_response"]["value"] == "Late"
    restored = WaitBook(book.snapshot())
    pending = restored.due(NOW + timedelta(minutes=10))
    assert len(pending) == 1
    command = restored.command_id(pending[0])
    assert not restored.observe("unrelated", None)
    assert restored.observe(command, None)
    assert restored.due(NOW + timedelta(days=1)) == []
    assert not restored.observe(command, None)


def test_native_receipts_are_not_ack_or_task_completion():
    book, item = create(**alert())
    assert date(item["deadline_at"]) == NOW + timedelta(minutes=6)
    book.receipt("w", "phone", "armed", NOW)
    with pytest.raises(ValueError, match="not started"):
        book.receipt("w", "phone", "acknowledged", NOW)
    fired = NOW + timedelta(minutes=5)
    for event in ("fired", "permission_denied", "error", "auto_stopped"):
        assert book.receipt("w", "phone", event, fired)["status"] == "waiting"
    assert book.reply("Unrelated conversation", fired) is None
    result = book.receipt("w", "phone", "acknowledged", fired)
    assert result["status"] == "acknowledged" and result["delivery_state"] == "pending"
    first = result["receipts"]["phone"]["acknowledged"]
    assert book.receipt("w", "phone", "acknowledged", fired + timedelta(seconds=1))["receipts"]["phone"]["acknowledged"] == first
    assert len(book.due(fired)) == 1


def test_cancel_suppresses_resume_and_native_schedule():
    book, _ = create(**alert())
    book.resolve("w", "cancelled", NOW)
    assert book.alerts(NOW)[0]["cancelled"]
    assert book.due(NOW + timedelta(days=1)) == []


def test_interrupted_delivery_retries_are_bounded_and_ids_stable():
    book, _ = create()
    for attempt in range(3):
        item = book.due(NOW + timedelta(minutes=1))[0]
        assert item["delivery_attempt"] == attempt
        command = book.command_id(item)
        assert book.command_id(book.due(NOW + timedelta(minutes=2))[0]) == command
        book.observe(command, "interrupted")
    assert book.get("w")["delivery_state"] == "failed"
    assert book.due(NOW + timedelta(days=1)) == []


@pytest.mark.parametrize("changes", [
    {"timeout_seconds": True}, {"timeout_seconds": 0}, {"timeout_seconds": 2678401},
    {"name": " "}, {"criticality": "urgent"}, {"response_kind": "yes"}, {"unknown": 0},
    {"alert": {"at": stamp(NOW), "kind": "alarm", "title": "x"}},
    {"response_kind": "ack", "alert": {"at": "2026-10-20T12:00:00", "kind": "message", "title": "x"}},
])
def test_invalid_waits_are_rejected(changes):
    with pytest.raises(ValueError):
        create(**changes)


def test_service_dispatch_persistence_and_user_observer(tmp_path, monkeypatch):
    async def run():
        now = NOW
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: now)
        sent = []
        async def deliver(command):
            sent.append(command)
        service = SchedulerService(tmp_path / "scheduler.json", deliver, retry_interval=60)
        await service.wait_call("create", {"wait_id": "w", "wait": {"name": "Reply", "context": "<untrusted>", "timeout_seconds": 60}})
        command = PromptRequest(id="human", prompt="yes", prompt_role="user")
        await service.observe(CommandAccepted(request_id=command.id, command=command))
        assert service.waits.get("w")["status"] == "answered"
        await service.wait_call("create", {"wait_id": "next", "wait": {"name": "Next", "context": "<untrusted>", "timeout_seconds": 60}})
        now += timedelta(minutes=1)
        await service.dispatch_due(now)
        assert len(sent) == 1 and sent[0].prompt_kind == "scheduler_wait"
        assert "&lt;untrusted&gt;" in sent[0].prompt
        await service.dispatch_due(now)
        assert len(sent) == 1
        restored = SchedulerService(service.path, deliver)
        restored.wakes = restored._load()
        await restored.dispatch_due(now)
        assert sent[0].id == sent[1].id
        await restored.observe(CommandCompleted(request_id=sent[1].id, command=sent[1]))
        await restored.dispatch_due(now + timedelta(days=1))
        assert len(sent) == 2
    asyncio.run(run())


def test_failed_save_rolls_back_wait_mutations(tmp_path, monkeypatch):
    async def run():
        service = SchedulerService(tmp_path / "state.json", None)
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: NOW)
        def fail():
            raise OSError("disk full")
        monkeypatch.setattr(service, "_save", fail)
        with pytest.raises(OSError):
            await service.wait_call("create", {"wait_id": "w", "wait": {"name": "x", "context": "x", "timeout_seconds": 10}})
        assert service.waits.snapshot() == []
    asyncio.run(run())


def test_native_projection_is_read_only_and_occurrence_scoped(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr("the_framework.plugins.scheduler.service.utc_now", lambda: NOW)
        service = SchedulerService(tmp_path / "state.json", None)
        await service.create(wake_id="wake", name="Wake", prompt="Private instructions", at=stamp(NOW + timedelta(minutes=5)), delivery="alarm")
        await service.task_call("create", {"task_id": "t", "task": {"title": "Task", "reminder_at": stamp(NOW + timedelta(minutes=10))}})
        await service.wait_call("create", {"wait_id": "w", "wait": {"name": "Wait", "context": "Private context", "timeout_seconds": 60, **alert()}})
        before = service.path.read_bytes()
        first = await service.native_alerts()
        assert first == await service.native_alerts() and not first["truncated"]
        assert len(first["alerts"]) == 3 and service.path.read_bytes() == before
        assert "Private" not in str(first)
        assert next(r for r in first["alerts"] if r["id"].startswith("wake-"))["kind"] == "alarm"
        assert next(r for r in first["alerts"] if r["id"].startswith("task-"))["kind"] == "message"
        old_id = next(r["id"] for r in first["alerts"] if r["id"].startswith("task-"))
        await service.task_call("update", {"task_id": "t", "changes": {"reminder_at": stamp(NOW + timedelta(hours=1))}, "expected_revision": 1})
        assert old_id not in [r["id"] for r in (await service.native_alerts())["alerts"]]
        await service.task_call("resolve", {"task_id": "t", "action": "done", "expected_revision": 2})
        assert len((await service.native_alerts())["alerts"]) == 2
        assert service.waits.get("w")["status"] == "waiting"
    asyncio.run(run())


@pytest.mark.parametrize('action', ['answered', 'acknowledged', 'cancelled', 'timed_out'])
def test_wait_provider_drops_settled_outcomes_but_keeps_pending_delivery(tmp_path, monkeypatch, action):
    from the_framework.agent import Agent
    from the_framework.agent.extensions.providers import Provider
    from the_framework.plugins.scheduler import SchedulerExtension

    async def run():
        monkeypatch.setattr('the_framework.plugins.scheduler.service.utc_now', lambda: NOW)
        service = SchedulerService(tmp_path / 'state.json', None)
        agent = Agent(client=object())
        calls = []

        async def request(call):
            calls.append(call)
            return await service.wait_call(call.method.removeprefix('wait_'), call.payload)

        agent.bridge.bind(request)
        plugin = agent.add_extension(SchedulerExtension)
        provider = Provider.from_function(plugin.response_waits)
        assert await provider.aoutputs() == []
        kind = 'ack' if action == 'acknowledged' else 'reply'
        await service.wait_call('create', {'wait_id': 'w', 'wait': {
            'name': 'Wait', 'context': 'Useful now', 'timeout_seconds': 60, 'response_kind': kind}})
        assert (await plugin.response_waits())['active'][0]['id'] == 'w'
        if action == 'timed_out':
            service.waits.due(NOW + timedelta(seconds=60))
        else:
            service.waits.resolve('w', action, NOW, resume=action == 'acknowledged')
        if action in {'acknowledged', 'timed_out'}:
            result = await plugin.response_waits()
            assert result['active'][0]['status'] == action
            assert 'recent' not in result
            record = service.waits.get('w')
            service.waits.observe(service.waits.command_id(record), None)
        assert await provider.aoutputs() == []
        assert all(c.payload == {'active_only': True} for c in calls)
        # Removal from model context is not deletion of history or receipt evidence.
        assert len((await service.wait_call('list', {'active_only': False}))['waits']) == 1
        assert (await service.wait_call('get', {'wait_id': 'w'}))['status'] == action
    asyncio.run(run())


def test_wait_provider_bounds_current_context_and_reports_unavailable_state():
    from the_framework.agent import Agent
    from the_framework.plugins.scheduler import SchedulerExtension

    async def run():
        agent = Agent(client=object())
        records = [{'id': str(i), 'status': 'waiting', 'context': 'x' * 16000,
                    'alert': {'body': 'private'}, 'receipts': {'phone': {}}} for i in range(13)]
        async def request(call):
            assert call.payload == {'active_only': True}
            return {'waits': records}
        agent.bridge.bind(request)
        plugin = agent.add_extension(SchedulerExtension)
        result = await plugin.response_waits()
        assert result['active_total'] == 13 and result['truncated']
        assert len(result['active']) == 12
        assert all(len(r['context']) == 1200 and 'alert' not in r and 'receipts' not in r
                   for r in result['active'])
        async def fail(call):
            raise RuntimeError('scheduler offline')
        agent.bridge.bind(fail)
        assert await plugin.response_waits() == {'status': 'unavailable', 'error': 'scheduler offline'}
    asyncio.run(run())
