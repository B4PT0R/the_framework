import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent.runtime.protocol import StatusRequest
from core.server.runtime.supervisor import WorkerExited, WorkerSupervisor


@pytest.mark.parametrize("broken_pipe", [False, True])
def test_shutdown_escalates_when_command_delivery_cannot_complete(tmp_path, broken_pipe):
    async def run():
        class Supervisor(WorkerSupervisor):
            async def send(self, request):
                if broken_pipe:
                    raise BrokenPipeError("worker stopped reading")
                await asyncio.Event().wait()

            async def kill(self):
                self.process.returncode = -15

        supervisor = Supervisor(tmp_path / "session.json", stop_timeout=0.01)
        supervisor.process = SimpleNamespace(returncode=None)
        await asyncio.wait_for(supervisor.stop(), 1)
        assert not supervisor.running

    asyncio.run(run())


def test_full_output_queue_cannot_block_process_shutdown(tmp_path):
    async def run():
        fixture = Path(__file__).parents[1] / "fixtures" / "fake_worker.py"
        supervisor = WorkerSupervisor(
            tmp_path / "session.json", command=[sys.executable, str(fixture)],
            stop_timeout=0.1,
        )
        supervisor.outputs = asyncio.Queue(1)
        await supervisor.start()
        supervisor.outputs.put_nowait("backlog")
        for index in range(4):
            await supervisor.send(StatusRequest(id=str(index)))
        await asyncio.wait_for(supervisor.stop(), 2)
        assert not supervisor.running
        assert supervisor.reader_task.done()
        assert supervisor.stderr_task.done()
        assert isinstance(supervisor.outputs.get_nowait(), WorkerExited)

    asyncio.run(run())
