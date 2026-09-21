import asyncio

from harness_core.agent.runtime.protocol import CommandCompleted, CommandFailed, PromptRequest

from harness_core.plugins.system.service import ServerRestartIntent, SystemControlService


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
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None)
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
        service = SystemControlService(path, lambda command: None, restart=lambda intent: None)
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


def test_realtime_delegation_restart_waits_for_realtime_and_rebinds(tmp_path):
    async def test():
        delivered = []
        queued = []
        restarted = []
        path = tmp_path / "system-control.json"
        service = SystemControlService(path, delivered.append, restart=restarted.append)
        await service.handle(
            "reboot_server",
            {"action_id": "restart-voice", "resume_instruction": "Continue le test."},
            command_id="realtime-delegation:old-call:old-delegation",
        )
        source = PromptRequest(
            id="realtime-delegation:old-call:old-delegation",
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
