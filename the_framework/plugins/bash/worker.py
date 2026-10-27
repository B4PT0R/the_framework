"""Bash's bounded request protocol over the common duplex process channel."""

import asyncio
from contextlib import suppress
import json
from pathlib import Path
import signal
import socket
import sys
import threading

from the_framework.execution import launch_process

MAX_FRAME = 16 * 1024 * 1024
METHODS = {"command", "python", "write", "edit", "job", "check", "interrupt"}


def recipe(fd):
    return [sys.executable, "-m", "the_framework.plugins.bash.worker", str(fd)]


def encode(value):
    frame = json.dumps(value, allow_nan=False, default=_path).encode() + b"\n"
    if len(frame) > MAX_FRAME:
        raise ValueError("Bash protocol frame is too large")
    return frame


def _path(value):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def decode(frame):
    if not frame:
        raise ConnectionError("Bash worker channel closed; jobs are unavailable")
    if len(frame) > MAX_FRAME or not frame.endswith(b"\n"):
        raise ValueError("invalid Bash protocol frame")
    return json.loads(frame)


class BashClient:
    def __init__(self, target, config):
        self._lock = threading.Lock()
        self.process = launch_process(target, "bash", recipe)
        self._stream = self.process.socket.makefile("rb")
        # Bound initialization, not user command lifetime.
        self.process.socket.settimeout(15)
        try:
            self.process.socket.sendall(encode({"config": config}))
            ready = self._receive()
            if ready != {"ready": True}:
                raise RuntimeError("Bash worker failed to initialize")
        except BaseException:
            self.close()
            raise
        self.process.socket.settimeout(None)

    def _receive(self):
        reply = decode(self._stream.readline(MAX_FRAME + 1))
        if "error" in reply:
            errors = {"ValueError": ValueError, "FileNotFoundError": FileNotFoundError,
                      "RuntimeError": RuntimeError, "TypeError": TypeError,
                      "PermissionError": PermissionError}
            raise errors.get(reply["error"], RuntimeError)(reply["message"])
        return reply["result"]

    def call(self, method, **arguments):
        if method not in METHODS:
            raise ValueError("unknown Bash operation")
        # Serialize before sending anything: a bad local argument must not break
        # synchronization of the next request/reply.
        frame = encode({"method": method, "arguments": arguments})
        with self._lock:
            self.process.socket.sendall(frame)
            return self._receive()

    def close(self):
        try:
            self.process.close()
        finally:
            self._stream.close()


async def serve(fd):
    from .engine import BashEngine

    connection = socket.socket(fileno=fd)
    connection.setblocking(False)
    reader, writer = await asyncio.open_connection(sock=connection, limit=MAX_FRAME)
    engine = None
    pending = set()
    loop = asyncio.get_running_loop()

    async def dispatch(request):
        try:
            method = request["method"]
            if method not in METHODS:
                raise ValueError("unknown Bash operation")
            result = await asyncio.to_thread(getattr(engine, method), **request["arguments"])
            reply = {"result": result}
        except Exception as error:
            reply = {"error": type(error).__name__, "message": str(error)}
        # The owner may have disconnected while a command was completing.
        with suppress(ConnectionError, OSError):
            writer.write(encode(reply))
            await writer.drain()

    def interrupt():
        if engine is not None:
            task = asyncio.create_task(asyncio.to_thread(engine._stop_all_processes, interrupted=True))
            pending.add(task)
            task.add_done_callback(pending.discard)

    owner = asyncio.current_task()
    loop.add_signal_handler(signal.SIGUSR1, interrupt)
    for number in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(number, owner.cancel)
    try:
        request = decode(await asyncio.wait_for(reader.readline(), 15))
        engine = await asyncio.to_thread(BashEngine, request["config"])
        writer.write(encode({"result": {"ready": True}}))
        await writer.drain()
        active = None
        while frame := await reader.readline():
            request = decode(frame)
            if active is not None and not active.done():
                raise ValueError("concurrent Bash requests are not supported")
            active = asyncio.create_task(dispatch(request))
            pending.add(active)
            active.add_done_callback(pending.discard)
    except asyncio.CancelledError:
        pass  # Termination signals use the same cooperative cleanup as EOF.
    finally:
        for number in (signal.SIGUSR1, signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(number)
        if engine is not None:
            await asyncio.to_thread(engine.close)
        await asyncio.gather(*pending, return_exceptions=True)
        writer.close()
        with suppress(ConnectionError, OSError):
            await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(serve(int(sys.argv[1])))
