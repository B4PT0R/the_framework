import asyncio
from datetime import datetime, timedelta, timezone

from harness_core.agent.runtime.protocol import (
    ApplicationCall,
    ApplicationRequest,
    ApplicationResult,
    CommandCompleted,
    CommandFailed,
    PromptRequest,
)

from harness_core.server.runtime.bridge import ApplicationBridge
from harness_core.plugins.scheduler.service import SchedulerService


def iso(value):
    return value.isoformat()


def test_one_time_wake_is_persisted_delivered_and_completed_once(tmp_path):
    async def test():
        delivered = []

        async def deliver(command):
            delivered.append(command)

        path = tmp_path / "scheduler.json"
        service = SchedulerService(path, deliver)
        wake = await service.create(
            wake_id="wake-1",
            name="Tea",
            prompt="Ask whether the tea was good.",
            at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
        )
        await service.dispatch_due()

        assert path.exists()
        assert len(delivered) == 1
        command = delivered[0]
        assert isinstance(command, PromptRequest)
        assert command.prompt_role == "developer"
        assert command.prompt_kind == "scheduled_wake"
        assert '<scheduled_wake id="wake-1" name="Tea"' in command.prompt
        assert "Ask whether the tea was good." in command.prompt

        completed = PromptRequest(**dict(command))
        completed.status = "completed"
        await service.observe(CommandCompleted(
            request_id=command.id,
            command=completed,
        ))
        listed = await service.list()
        assert listed["wakes"][0]["enabled"] is False
        assert listed["wakes"][0]["last_run_at"] is not None

        restored = SchedulerService(path, deliver, poll_interval=60)
        await restored.start()
        try:
            assert restored.wakes[wake["id"]].enabled is False
            await restored.dispatch_due()
            assert len(delivered) == 1
        finally:
            await restored.stop()

    asyncio.run(test())


def test_periodic_wake_advances_and_interrupted_delivery_gets_a_new_id(tmp_path):
    async def test():
        delivered = []

        async def deliver(command):
            delivered.append(command)

        service = SchedulerService(tmp_path / "scheduler.json", deliver, retry_interval=0)
        await service.create(
            wake_id="periodic",
            name="Check in",
            prompt="Check in warmly.",
            interval_minutes=15,
            first_at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
        )
        await service.dispatch_due()
        first = delivered[-1]
        failed = PromptRequest(**dict(first))
        failed.status = "failed"
        await service.observe(CommandFailed(
            request_id=first.id,
            command=failed,
            error="interrupted",
        ))
        await service.dispatch_due()
        second = delivered[-1]

        assert second.id != first.id

        completed = PromptRequest(**dict(second))
        completed.status = "completed"
        await service.observe(CommandCompleted(
            request_id=second.id,
            command=completed,
        ))
        wake = (await service.list())["wakes"][0]
        assert wake["enabled"] is True
        assert datetime.fromisoformat(wake["next_run_at"]) > datetime.now(timezone.utc)

    asyncio.run(test())


def test_alarm_delivery_is_explicit_and_survives_persistence(tmp_path):
    async def test():
        delivered = []

        async def deliver(command):
            delivered.append(command)

        path = tmp_path / "scheduler.json"
        service = SchedulerService(path, deliver)
        wake = await service.create(
            wake_id="alarm-1",
            name="Réveil",
            prompt="Wake Baptiste warmly.",
            at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
            delivery="alarm",
        )
        await service.dispatch_due()

        assert wake["delivery"] == "alarm"
        assert delivered[0].prompt_kind == "scheduled_alarm"
        assert 'delivery="alarm"' in delivered[0].prompt
        restored = SchedulerService(path, deliver)
        assert restored._load()["alarm-1"].delivery == "alarm"

    asyncio.run(test())


def test_scheduler_rejects_naive_dates_and_ambiguous_schedules(tmp_path):
    async def test():
        service = SchedulerService(tmp_path / "scheduler.json", lambda command: None)
        try:
            await service.create(
                wake_id="bad-date",
                name="Bad",
                prompt="No offset.",
                at="2026-08-14T08:00:00",
            )
        except ValueError as error:
            assert "UTC offset" in str(error)
        else:
            raise AssertionError("naive date was accepted")

        try:
            await service.create(
                wake_id="ambiguous",
                name="Bad",
                prompt="Two schedules.",
                at=iso(datetime.now(timezone.utc)),
                interval_minutes=10,
            )
        except ValueError as error:
            assert "exactly one" in str(error)
        else:
            raise AssertionError("ambiguous schedule was accepted")

    asyncio.run(test())


def test_application_bridge_resolves_server_capabilities_without_frontend_runtime():
    async def test():
        class Supervisor:
            def __init__(self):
                self.sent = []

            async def send(self, request):
                self.sent.append(request)

        supervisor = Supervisor()
        bridge = ApplicationBridge(supervisor)

        async def handler(method, payload):
            return {"method": method, "value": payload["value"]}

        bridge.register("scheduler", handler)
        await bridge.dispatch(ApplicationCall(
            request_id="call-1",
            request=ApplicationRequest(
                id="call-1",
                capability="scheduler",
                method="echo",
                payload={"value": 42},
            ),
        ))

        assert bridge.connected is False
        assert len(supervisor.sent) == 1
        result = supervisor.sent[0]
        assert isinstance(result, ApplicationResult)
        assert result.status == "success"
        assert result.result == {"method": "echo", "value": 42}

    asyncio.run(test())


def test_application_bridge_passes_the_active_command_to_contextual_capabilities():
    async def test():
        class Supervisor:
            def __init__(self):
                self.sent = []

            async def send(self, request):
                self.sent.append(request)

        supervisor = Supervisor()
        bridge = ApplicationBridge(supervisor)
        contexts = []

        async def handler(method, payload, *, command_id):
            contexts.append((method, payload, command_id))
            return {"accepted": True}

        bridge.register("system", handler, contextual=True)
        await bridge.dispatch(ApplicationCall(
            request_id="call-2",
            command_id="turn-42",
            request=ApplicationRequest(
                id="call-2",
                capability="system",
                method="reboot_server",
                payload={"action_id": "restart-42"},
            ),
        ))

        assert contexts == [(
            "reboot_server",
            {"action_id": "restart-42"},
            "turn-42",
        )]
        assert supervisor.sent[0].status == "success"

    asyncio.run(test())


def test_inactive_scheduler_keeps_due_wakes_without_dispatching(tmp_path):
    async def test():
        delivered = []

        async def deliver(command):
            delivered.append(command)

        service = SchedulerService(tmp_path / "scheduler.json", deliver)
        await service.create(
            wake_id="paused",
            name="Paused",
            prompt="Do not run while the plugin is inactive.",
            at=iso(datetime.now(timezone.utc) - timedelta(seconds=1)),
        )
        service.active = False
        await service.dispatch_due()

        assert delivered == []
        assert (await service.list())["wakes"][0]["enabled"] is True

    asyncio.run(test())
