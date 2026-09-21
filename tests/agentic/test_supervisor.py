import asyncio
import sys
from pathlib import Path

from harness_core.agent.runtime.protocol import (
    SessionSnapshot,
    SessionSnapshotRequest,
    StatusRequest,
    WorkerStatus,
)
from harness_core.server.runtime.supervisor import WorkerExited, WorkerSupervisor, WorkerTransportError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"


def test_supervisor_defaults_to_the_generic_agent_worker(tmp_path):
    supervisor = WorkerSupervisor(tmp_path / "session.json")

    assert supervisor.command[1:3] == ["-m", "harness_core.agent.runtime.worker_process"]


def test_supervisor_starts_exchanges_messages_and_stops(tmp_path):
    async def test():
        supervisor = WorkerSupervisor(
            tmp_path / "session.json",
            command=[sys.executable, str(FIXTURE)],
        )

        ready = await supervisor.start()
        await supervisor.send(StatusRequest(id="status"))
        status = await supervisor.outputs.get()

        assert ready.session_id == "test"
        assert isinstance(status, WorkerStatus)
        assert status.request_id == "status"

        await supervisor.stop()
        assert supervisor.process.returncode == 0

    asyncio.run(test())


def test_supervisor_forces_a_blocked_worker_to_stop(tmp_path):
    async def test():
        supervisor = WorkerSupervisor(
            tmp_path / "session.json",
            command=[sys.executable, str(FIXTURE), "--hang-on-shutdown"],
            stop_timeout=0.05,
        )

        await supervisor.start()
        await supervisor.stop()

        assert supervisor.process.returncode is not None

    asyncio.run(test())


def test_supervisor_observes_exit_and_can_restart(tmp_path):
    async def test():
        supervisor = WorkerSupervisor(
            tmp_path / "session.json",
            command=[sys.executable, str(FIXTURE), "--exit-after-ready"],
        )

        await supervisor.start()
        exited = await supervisor.outputs.get()
        assert isinstance(exited, WorkerExited)
        assert exited.returncode == 3

        supervisor.command = [sys.executable, str(FIXTURE)]
        ready = await supervisor.restart()
        assert ready.session_id == "test"
        await supervisor.stop()

    asyncio.run(test())


def test_supervisor_terminates_a_worker_with_broken_output(tmp_path):
    async def test():
        supervisor = WorkerSupervisor(
            tmp_path / "session.json",
            command=[sys.executable, str(FIXTURE), "--malformed-after-ready"],
        )

        await supervisor.start()
        error = await supervisor.outputs.get()
        exited = await supervisor.outputs.get()

        assert isinstance(error, WorkerTransportError)
        assert isinstance(exited, WorkerExited)
        assert supervisor.running is False

    asyncio.run(test())


def test_supervisor_accepts_json_lines_larger_than_asyncio_default(tmp_path):
    async def test():
        supervisor = WorkerSupervisor(
            tmp_path / "session.json",
            command=[sys.executable, str(FIXTURE), "--large-session-snapshot"],
        )

        await supervisor.start()
        await supervisor.send(SessionSnapshotRequest(id="large-session"))
        snapshot = await supervisor.outputs.get()

        assert isinstance(snapshot, SessionSnapshot)
        assert snapshot.request_id == "large-session"
        assert len(snapshot.instructions) == 128_000
        assert supervisor.running is True

        await supervisor.stop()

    asyncio.run(test())
