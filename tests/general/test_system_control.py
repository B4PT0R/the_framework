import asyncio

import pytest
from unittest.mock import AsyncMock

from the_framework.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest

from the_framework.plugins.system.service import ServerRestartIntent, SystemControlService


def terminal(command, *, status="completed", error=None):
    command.status = status
    output_type = CommandCompleted if error is None else CommandFailed
    kwargs = {
        "request_id": command.id,
        "command": command,
    }
    if error is not None:
        kwargs["error"] = error
    return output_type(**kwargs)


def test_realtime_recovery_status_reflects_committed_intent(tmp_path):
    async def scenario():
        path = tmp_path / "system-control.json"
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None)
        service.intent = ServerRestartIntent(
            action_id="restart-voice",
            source_command_id="realtime-delegation:call:item",
            resume_instruction="Reprendre la voix",
            status="restart_committed",
            created_at="2026-08-16T00:00:00+00:00",
            committed_at="2026-08-16T00:00:01+00:00",
            resume_route="realtime_delegation",
        )
        assert await service.realtime_recovery_status() == {
            "resume_required": True,
            "action_id": "restart-voice",
            "resume_bound": False,
        }
        service.intent.resume_command_id = "realtime-delegation:call:new-item"
        assert (await service.realtime_recovery_status())["resume_bound"] is True
        service.intent = None
        assert await service.realtime_recovery_status() == {
            "resume_required": False,
            "action_id": None,
            "resume_bound": False,
        }

    asyncio.run(scenario())


def test_new_restart_supersedes_unbound_committed_realtime_resume(tmp_path):
    async def test():
        path = tmp_path / "system-control.json"
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None, realtime_call_id=lambda: "old-call")
        await service.handle(
            "reboot_server",
            {"action_id": "restart-old", "resume_instruction": "Old resume."},
            command_id="realtime-delegation:old-call:old-item",
        )
        old_command = PromptRequest(
            id="realtime-delegation:old-call:old-item",
            prompt="old",
        )
        await service.observe(terminal(old_command))
        await service.restart_task
        assert service.intent.status == "restart_committed"
        assert service.intent.resume_command_id is None

        result = await service.handle(
            "reboot_server",
            {"action_id": "restart-new", "resume_instruction": "New resume."},
            command_id="turn-new",
        )

        assert result["action_id"] == "restart-new"
        assert service.intent.action_id == "restart-new"
        assert service.intent.status == "awaiting_turn_completion"
        assert service.intent.source_command_id == "turn-new"

    asyncio.run(test())


def test_new_restart_supersedes_bound_committed_realtime_resume(tmp_path):
    async def test():
        path = tmp_path / "system-control.json"
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None, realtime_call_id=lambda: "old-call")
        await service.handle(
            "reboot_server",
            {"action_id": "restart-old", "resume_instruction": "Old resume."},
            command_id="realtime-delegation:old-call:old-item",
        )
        old_command = PromptRequest(
            id="realtime-delegation:old-call:old-item",
            prompt="old",
        )
        await service.observe(terminal(old_command))
        await service.restart_task
        assert await service.bind_realtime_resume(
            "restart-old",
            "realtime-delegation:recovered-call:recovered-item",
        ) is True

        result = await service.handle(
            "reboot_server",
            {"action_id": "restart-new", "resume_instruction": "New resume."},
            command_id="realtime-delegation:current-call:current-item",
        )

        assert result["action_id"] == "restart-new"
        assert service.intent.action_id == "restart-new"
        assert service.intent.status == "awaiting_turn_completion"
        assert service.intent.resume_command_id is None

    asyncio.run(test())


def test_reboot_waits_for_turn_completion_then_resumes_once(tmp_path):
    async def test():
        delivered = []
        restarted = []
        path = tmp_path / "system-control.json"

        async def deliver(command):
            delivered.append(command)

        service = SystemControlService(path, deliver, restart=restarted.append)
        result = await service.handle(
            "reboot_server",
            {
                "action_id": "restart-1",
                "resume_instruction": "Continue les vérifications après le reboot.",
            },
            command_id="turn-1",
        )

        assert result["restart_after_command_id"] == "turn-1"
        assert path.exists()
        assert restarted == []

        await service.observe(terminal(PromptRequest(id="other", prompt="other")))
        assert restarted == []
        await service.observe(terminal(PromptRequest(id="turn-1", prompt="current")))
        await service.restart_task

        assert restarted[0]["status"] == "restart_committed"
        assert restarted[0]["source_command_id"] == "turn-1"

        restored = SystemControlService(path, deliver, restart=restarted.append)
        await restored.start()

        assert len(delivered) == 1
        resume = delivered[0]
        assert resume.id == "system-resume-restart-1"
        assert resume.prompt_role == "developer"
        assert resume.prompt_kind == "system_resume"
        assert "Continue les vérifications" in resume.prompt

        await restored.observe(terminal(PromptRequest(**dict(resume))))
        assert not path.exists()
        assert restored.intent is None

    asyncio.run(test())


@pytest.mark.parametrize("source_id", ["realtime-delegation:old-call:old-delegation", "goal-123-4", "text-turn"])
def test_active_voice_restart_waits_for_owning_client_and_rebinds(tmp_path, source_id):
    async def test():
        delivered = []
        queued = []
        restarted = []
        path = tmp_path / "system-control.json"
        service = SystemControlService(path, delivered.append, restart=restarted.append, realtime_call_id=lambda: "old-call")
        await service.handle(
            "reboot_server",
            {"action_id": "restart-voice", "resume_instruction": "Continue le test."},
            command_id=source_id,
        )
        source = PromptRequest(
            id=source_id,
            prompt="test",
        )
        await service.observe(terminal(source))
        await service.restart_task

        restored = SystemControlService(
            path,
            delivered.append,
            restart=restarted.append,
            resume_realtime=queued.append,
        )
        await restored.start()

        assert delivered == []
        assert len(queued) == 1
        assert queued[0]["resume_route"] == "realtime_delegation"
        assert restored.intent.resume_command_id is None
        assert queued[0]["resume_call_id"] == "old-call"

        command_id = "realtime-delegation:new-call:new-delegation"
        assert await restored.bind_realtime_resume("restart-voice", command_id) is True
        rebound = PromptRequest(id=command_id, prompt="resume")
        await restored.observe(terminal(rebound))
        assert restored.intent is None
        assert not path.exists()

    asyncio.run(test())


def test_unfinished_or_interrupted_turn_never_reboots_later(tmp_path):
    async def test():
        path = tmp_path / "system-control.json"
        restarted = []
        service = SystemControlService(path, lambda command: None, restart=restarted.append)
        await service.handle(
            "reboot_server",
            {"action_id": "restart-2"},
            command_id="turn-2",
        )
        interrupted = PromptRequest(id="turn-2", prompt="current")
        await service.observe(terminal(
            interrupted,
            status="interrupted",
            error="interrupted",
        ))

        assert restarted == []
        assert not path.exists()

        await service.handle(
            "reboot_server",
            {"action_id": "restart-3"},
            command_id="turn-3",
        )
        restored = SystemControlService(path, lambda command: None, restart=restarted.append)
        await restored.start()

        assert restarted == []
        assert restored.intent is None
        assert not path.exists()

    asyncio.run(test())


def test_reloading_after_reset_clears_stale_state_error(tmp_path):
    async def scenario():
        path = tmp_path / "system-control.json"
        path.write_text("invalid restart state")
        service = SystemControlService(path, lambda command: None)
        await service.start()
        assert service.error is not None

        path.unlink()
        await service.start()
        assert service.error is None
        assert service.intent is None

    asyncio.run(scenario())


def test_failed_restart_can_be_retried_without_a_phantom_resume(tmp_path):
    async def scenario():
        calls = []
        async def restart(intent):
            calls.append(intent["action_id"])
            if len(calls) == 1:
                raise TimeoutError("voice still active")
        path = tmp_path / "control.json"
        service = SystemControlService(path, lambda command: None, restart=restart)
        for index in range(2):
            command = PromptRequest(id=f"turn-{index}", prompt="Restart")
            await service.handle("reboot_server", {
                "action_id": f"restart-{index}", "resume_instruction": "Continue.",
            }, command_id=command.id)
            await service.observe(terminal(command))
            await service.restart_task
            if index == 0:
                assert service.error is None
                assert service.intent is None
                assert not path.exists()
                restored = SystemControlService(path, lambda command: None)
                await restored.start()
                assert restored.intent is None
        assert calls == ["restart-0", "restart-1"]
        assert service.intent.action_id == "restart-1"
    asyncio.run(scenario())


def test_running_realtime_restart_cannot_be_superseded(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def restart(intent):
            started.set()
            await release.wait()
        service = SystemControlService(tmp_path / "control.json", lambda command: None, restart=restart, realtime_call_id=lambda: "old-call")
        command = PromptRequest(id="realtime-delegation:call:turn", prompt="Restart")
        await service.handle("reboot_server", {
            "action_id": "first", "resume_instruction": "Continue.",
        }, command_id=command.id)
        await service.observe(terminal(command))
        await started.wait()
        try:
            try:
                await service.handle("reboot_server", {
                    "action_id": "second", "resume_instruction": "Continue.",
                }, command_id="another-turn")
            except RuntimeError as error:
                assert "already" in str(error)
            else:
                raise AssertionError("A concurrent restart was accepted")
            assert service.intent.action_id == "first"
        finally:
            release.set()
            await service.restart_task
    asyncio.run(scenario())


def test_failed_restart_cleanup_error_still_blocks_retry(tmp_path):
    async def scenario():
        async def restart(intent):
            raise TimeoutError("voice still active")
        service = SystemControlService(tmp_path / "control.json", lambda command: None, restart=restart)
        command = PromptRequest(id="turn", prompt="Restart")
        await service.handle("reboot_server", {
            "action_id": "first", "resume_instruction": "Continue.",
        }, command_id=command.id)
        def cannot_clear():
            raise OSError("storage unavailable")
        service._clear = cannot_clear
        await service.observe(terminal(command))
        try:
            await service.restart_task
        except OSError:
            pass
        else:
            raise AssertionError("Cleanup failure was hidden")
        assert "could not clear failed restart state" in service.error
        try:
            await service.handle("reboot_server", {"action_id": "retry"}, command_id="next")
        except RuntimeError as error:
            assert "storage unavailable" in str(error)
        else:
            raise AssertionError("Unsafe retry was accepted")
    asyncio.run(scenario())


def test_stopping_voice_before_restart_commit_resumes_text(tmp_path):
    async def run():
        active = "owning-tab"
        path = tmp_path / "control.json"
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None,
                                       realtime_call_id=lambda: active)
        command = PromptRequest(id="realtime-delegation:call:turn", prompt="restart")
        await service.handle("reboot_server", {"action_id": "restart"}, command_id=command.id)
        active = None
        await service.observe(terminal(command))
        await service.restart_task
        assert service.intent.resume_route == "agent"
        assert service.intent.resume_call_id is None
        delivered, queued = [], []
        restored = SystemControlService(path, AsyncMock(side_effect=delivered.append), resume_realtime=queued.append)
        await restored.start()
        assert len(delivered) == 1 and not queued
    asyncio.run(run())


@pytest.mark.parametrize("bind", [False, True])
def test_voice_resume_timeout_releases_agentic_work_without_duplicate_resume(tmp_path, bind):
    async def run():
        delivered, queued, cancelled = [], [], []
        path = tmp_path / "control.json"
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None,
                                       realtime_call_id=lambda: "owning-tab")
        command = PromptRequest(id="goal-123-4", prompt="restart")
        await service.handle("reboot_server", {"action_id": "restart"}, command_id=command.id)
        await service.observe(terminal(command))
        await service.restart_task
        restored = SystemControlService(path, AsyncMock(side_effect=delivered.append), resume_realtime=queued.append,
                                        cancel_realtime_resume=cancelled.append, resume_timeout=0)
        await restored.start()
        assert restored.awaiting_realtime_resume
        if bind:
            assert await restored.bind_realtime_resume("restart", "realtime-delegation:new:resume")
        await restored.resume_task
        assert not restored.awaiting_realtime_resume
        if bind:
            assert not delivered and not cancelled
        else:
            assert len(delivered) == 1 and cancelled == ["restart"]
            assert delivered[0].id == "system-resume-restart"
            assert restored.intent.resume_route == "agent"
            assert not await restored.bind_realtime_resume("restart", "late-delegation")
        assert restored.error is None
        await restored.stop()
    asyncio.run(run())


@pytest.mark.parametrize("unavailable", [True, False])
def test_voice_recovery_unavailable_still_falls_back_to_text(tmp_path, unavailable):
    async def run():
        service = SystemControlService(tmp_path / "control.json", AsyncMock(),
                                       restart=lambda intent: None,
                                       realtime_call_id=lambda: "owner")
        command = PromptRequest(id="goal-restart", prompt="restart")
        await service.handle("reboot_server", {"action_id": "restart"}, command_id=command.id)
        await service.observe(terminal(command))
        await service.restart_task
        restored = SystemControlService(service.path, AsyncMock(), resume_timeout=0,
            resume_realtime=None if unavailable else AsyncMock(side_effect=RuntimeError("offline")))
        await restored.start()
        await restored.resume_task
        restored.deliver.assert_awaited_once()
        assert restored.intent.resume_route == "agent"
        assert not restored.awaiting_realtime_resume
        await restored.stop()
    asyncio.run(run())
