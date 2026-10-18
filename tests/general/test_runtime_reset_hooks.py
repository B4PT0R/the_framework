"""Plugin-owned services remain coherent across canonical session resets."""

import asyncio
from types import SimpleNamespace

import pytest

from the_framework.server.runtime.session import SessionRuntime


class ResetRuntime(SessionRuntime):
    def __init__(self, path, events):
        self.supervisor = SimpleNamespace(session_path=path)
        self.reset_transaction_factory = lambda _path: ResetTransaction(
            events, fail_stage=self.fail_stage,
        )
        self.reset_lock = asyncio.Lock()
        self.resetting = False
        self.reset_hooks = []
        self.ready = "ready"
        self.events = events
        self.fail_start_once = False
        self.fail_stage = False

    async def stop(self):
        self.events.append("runtime.stop")

    async def start(self):
        self.events.append("runtime.start")
        if self.fail_start_once:
            self.fail_start_once = False
            raise RuntimeError("new worker failed")
        return self.ready


class ResetTransaction:
    def __init__(self, events, *, fail_stage=False):
        self.events = events
        self.fail_stage = fail_stage

    def stage(self):
        self.events.append("stage")
        if self.fail_stage:
            raise RuntimeError("staging failed")
        return self

    def restore(self, *, remove_new=False):
        self.events.append(("restore", remove_new))

    def commit(self):
        self.events.append("commit")


@pytest.mark.parametrize("fail_start", [False, True])
def test_per_call_transaction_uses_lifecycle_without_replacing_default(tmp_path, fail_start):
    async def scenario():
        default_events, selected_events = [], []
        runtime = ResetRuntime(tmp_path / "session.json", default_events)
        default = runtime.reset_transaction_factory
        runtime.fail_start_once = fail_start

        def selected(path):
            assert path == tmp_path / "session.json"
            return ResetTransaction(selected_events)

        if fail_start:
            with pytest.raises(RuntimeError, match="previous state was restored"):
                await runtime.reset_runtime_state(transaction_factory=selected)
            assert selected_events == ["stage", ("restore", True)]
        else:
            await runtime.reset_runtime_state(transaction_factory=selected)
            assert selected_events == ["stage", "commit"]
        assert runtime.reset_transaction_factory is default
        assert not runtime.resetting
        assert "stage" not in default_events
        await runtime.reset_runtime_state()
        assert default_events[-3:] == ["stage", "runtime.start", "commit"]

    asyncio.run(scenario())


def test_reset_suspends_dependents_in_reverse_order_and_resumes_after_commit(tmp_path):
    async def scenario():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)
        runtime.register_reset_hooks(
            suspend=lambda: events.append("first.suspend"),
            resume=lambda: events.append("first.resume"),
        )
        runtime.register_reset_hooks(
            suspend=lambda: events.append("second.suspend"),
            resume=lambda: events.append("second.resume"),
        )
        assert await runtime.reset_runtime_state() == "ready"
        assert not runtime.resetting
        assert events == [
            "second.suspend", "first.suspend", "runtime.stop", "stage",
            "runtime.start", "commit", "first.resume", "second.resume",
        ]

    asyncio.run(scenario())


def test_reset_restores_previous_runtime_before_resuming_services(tmp_path):
    async def scenario():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)
        runtime.fail_start_once = True
        runtime.register_reset_hooks(
            suspend=lambda: events.append("suspend"),
            resume=lambda: events.append("resume"),
        )
        with pytest.raises(RuntimeError, match="previous state was restored"):
            await runtime.reset_runtime_state()
        assert not runtime.resetting
        assert events == [
            "suspend", "runtime.stop", "stage", "runtime.start",
            "runtime.stop", ("restore", True), "runtime.start", "resume",
        ]

    asyncio.run(scenario())


def test_failed_suspend_resumes_earlier_services_without_touching_runtime(tmp_path):
    async def scenario():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)

        def fail():
            events.append("fail")
            raise RuntimeError("could not suspend")

        runtime.register_reset_hooks(suspend=fail, resume=lambda: events.append("not paused"))
        runtime.register_reset_hooks(
            suspend=lambda: events.append("suspend"),
            resume=lambda: events.append("resume"),
        )
        with pytest.raises(RuntimeError, match="could not suspend"):
            await runtime.reset_runtime_state()
        assert events == ["suspend", "fail", "resume"]
        assert not runtime.resetting

    asyncio.run(scenario())


def test_staging_failure_restarts_old_runtime_before_resuming_services(tmp_path):
    async def scenario():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)
        runtime.fail_stage = True
        runtime.register_reset_hooks(
            suspend=lambda: events.append("suspend"),
            resume=lambda: events.append("resume"),
        )
        with pytest.raises(RuntimeError, match="staging failed"):
            await runtime.reset_runtime_state()
        assert events == [
            "suspend", "runtime.stop", "stage", "runtime.start", "resume",
        ]
        assert not runtime.resetting

    asyncio.run(scenario())


def test_resume_failure_is_reported_after_committed_reset(tmp_path):
    async def scenario():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)

        def fail_resume():
            raise RuntimeError("resume failed")

        runtime.register_reset_hooks(suspend=lambda: None, resume=fail_resume)
        with pytest.raises(BaseExceptionGroup, match="failed to resume"):
            await runtime.reset_runtime_state()
        assert "commit" in events
        assert not runtime.resetting

    asyncio.run(scenario())


@pytest.mark.parametrize("fail_stage", [False, True])
def test_reset_accepts_close_only_and_refresh_only_hooks(tmp_path, fail_stage):
    async def run():
        events = []
        runtime = ResetRuntime(tmp_path / "session.json", events)
        runtime.fail_stage = fail_stage

        async def close():
            await asyncio.sleep(0)
            events.append("close")

        runtime.register_reset_hooks(resume=lambda: events.append("refresh"))
        runtime.register_reset_hooks(suspend=close)
        if fail_stage:
            with pytest.raises(RuntimeError, match="staging failed"):
                await runtime.reset_runtime_state()
        else:
            await runtime.reset_runtime_state()
        assert events == [
            "close", "runtime.stop", "stage", "runtime.start",
            *([] if fail_stage else ["commit"]), "refresh",
        ]
        assert not runtime.resetting

    asyncio.run(run())


def test_reset_rejects_empty_and_non_callable_hooks(tmp_path):
    runtime = ResetRuntime(tmp_path / "session.json", [])
    with pytest.raises(ValueError, match="at least one"):
        runtime.register_reset_hooks()
    for hooks in ({"suspend": False}, {"resume": 1}, {"suspend": lambda: None, "resume": "resume"}):
        with pytest.raises(TypeError, match="callable"):
            runtime.register_reset_hooks(**hooks)
    assert runtime.reset_hooks == []
