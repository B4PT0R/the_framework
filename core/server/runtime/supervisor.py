import asyncio
import json
import sys
from collections import deque

from ...agent.runtime.protocol import ShutdownRequest, WorkerOutput, WorkerReady
from ...agent.models.base import TypedBase
from core.utils.ids import timestamp_id


class WorkerExited(TypedBase):
    returncode: int
    stderr: list[str]


class WorkerTransportError(TypedBase):
    error: str


class WorkerSupervisor:
    def __init__(
        self,
        session_path,
        command=None,
        ready_timeout=10,
        stop_timeout=5,
        stream_limit_bytes=32 * 1024 * 1024,
    ):
        if stream_limit_bytes < 64 * 1024:
            raise ValueError("worker IPC stream limit must be at least 64 KiB")
        self.session_path = session_path
        self.command = command or [
            sys.executable,
            "-m",
            "core.agent.runtime.worker_process",
            "--session",
            str(session_path),
        ]
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.stream_limit_bytes = stream_limit_bytes
        self.process = None
        self.outputs = asyncio.Queue(512)
        self.stderr = deque(maxlen=100)
        self.reader_task = None
        self.stderr_task = None
        self.write_lock = asyncio.Lock()
        self.ready = None
        self.ready_output = None
        self.stopping = False

    @property
    def running(self):
        return self.process is not None and self.process.returncode is None

    async def start(self):
        if self.running:
            return self.ready_output
        self.stopping = False
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self.stream_limit_bytes,
        )
        self.ready = asyncio.get_running_loop().create_future()
        self.reader_task = asyncio.create_task(self._read_outputs())
        self.stderr_task = asyncio.create_task(self._read_stderr())
        try:
            self.ready_output = await asyncio.wait_for(self.ready, self.ready_timeout)
        except Exception:
            await self.kill()
            raise
        return self.ready_output

    async def _read_outputs(self):
        try:
            while line := await self.process.stdout.readline():
                output = WorkerOutput.from_dict(json.loads(line))
                if isinstance(output, WorkerReady) and not self.ready.done():
                    self.ready.set_result(output)
                else:
                    await self._publish(output)
        except Exception as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            else:
                await self._publish(WorkerTransportError(error=str(error)))
            if self.running:
                self.process.terminate()
        finally:
            returncode = await self.process.wait()
            if not self.ready.done():
                self.ready.set_exception(RuntimeError("worker exited before ready"))
            await self._publish(WorkerExited(
                returncode=returncode,
                stderr=list(self.stderr),
            ))

    async def _read_stderr(self):
        while line := await self.process.stderr.readline():
            self.stderr.append(line.decode(errors="replace").rstrip())

    async def send(self, request):
        if not self.running:
            raise RuntimeError("worker is not running")
        payload = json.dumps(request, separators=(",", ":")) + "\n"
        async with self.write_lock:
            self.process.stdin.write(payload.encode())
            await self.process.stdin.drain()

    async def stop(self):
        self._begin_stop()
        if not self.running:
            await self._finish_tasks()
            return
        try:
            async with asyncio.timeout(self.stop_timeout):
                await self.send(ShutdownRequest(id=timestamp_id()))
                await self.process.wait()
        except (TimeoutError, BrokenPipeError, ConnectionResetError):
            await self.kill()
        await self._finish_tasks()

    async def kill(self):
        self._begin_stop()
        if self.running:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), self.stop_timeout)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await self._finish_tasks()

    def _begin_stop(self):
        self.stopping = True
        # Release a reader already awaiting put() before shutdown began.
        if self.outputs.full():
            self.outputs.get_nowait()

    async def _publish(self, output):
        if not self.stopping:
            await self.outputs.put(output)
            return
        # No consumer is required during explicit shutdown. Keep the latest
        # diagnostics, including WorkerExited; canonical data lives in session.
        if self.outputs.full():
            self.outputs.get_nowait()
        self.outputs.put_nowait(output)

    async def restart(self):
        await self.kill()
        return await self.start()

    async def _finish_tasks(self):
        for task in (self.reader_task, self.stderr_task):
            if task is not None:
                await task
