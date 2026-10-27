"""A duplex byte channel and process lifetime, with host-owned launch recipes.

The channel carries a plugin's own protocol unchanged (including reverse RPC).
The host transport deliberately cannot choose commands, modules or executable paths:
applications register named recipes locally. Possession of its separate execution
credential is nevertheless a code-execution capability, not a security sandbox.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
import os
import signal
import socket
import subprocess
import threading
import time
from uuid import uuid4


class LocalProcess:
    """Own one process group and an inherited full-duplex socket.

    A recipe receives the inherited socket descriptor and returns argv. It is
    evaluated only where the process runs. No executable path crosses machines.
    """

    def __init__(self, recipe):
        parent, child = socket.socketpair()
        try:
            self.process = subprocess.Popen(
                recipe(child.fileno()), pass_fds=(child.fileno(),),
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
        self.socket = parent
        self._close_lock = threading.Lock()
        self._closed = False

    def poll(self):
        return self.process.poll()

    def send_signal(self, number, *, group=False):
        if self.poll() is None:
            with suppress(ProcessLookupError):
                (os.killpg if group else os.kill)(self.process.pid, number)

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def close(self):
        # EOF gives cooperative workers time to reap children in their own
        # sessions. Recipes must implement that cleanup; this is not a sandbox.
        with self._close_lock:
            if self._closed:
                return self.poll()
            self._closed = True
            with suppress(OSError):
                self.socket.shutdown(socket.SHUT_WR)
            try:
                self.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            finally:
                self.socket.close()
                # The group may still exist after its leader has exited.
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.wait()
            return self.poll()


class RemoteProcess:
    """Client handle; losing its channel always retires the remote process.

    ``endpoint`` supplies open_process(name) and process_control(handle, action).
    Control uses independent bounded requests so a busy duplex channel cannot
    prevent interruption. There is never a fallback to local execution.
    """

    def __init__(self, endpoint, name):
        self.endpoint = endpoint
        self.socket, self.handle = endpoint.open_process(name)
        self._closed = False
        self._returncode = None

    def poll(self):
        if self._closed:
            if self._returncode is None:
                raise RuntimeError("remote process status is unavailable")
            return self._returncode
        return self.endpoint.process_control(self.handle, "poll")

    def send_signal(self, number, *, group=False):
        if not self._closed:
            self.endpoint.process_control(self.handle, "signal", signal=int(number), group=group)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            result = self.poll()
            if result is not None:
                return result
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("remote process", timeout)
            time.sleep(.02)

    def close(self):
        if self._closed:
            return
        # Always release the local descriptor, even if the host disappeared.
        try:
            self._returncode = self.endpoint.process_control(self.handle, "close")
        finally:
            self._closed = True
            with suppress(OSError):
                self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()


class ProcessHost:
    """Host-owned recipe registry and bounded concurrent channel supervision.

    Authentication/listener policy belongs to the enclosing private host server.
    A recipe is opt-in application composition, never a module reference received
    from a client. Process handles are random per-channel capabilities. All
    operations and shutdown cleanup remain scoped to this host instance.
    """

    def __init__(self, recipes, *, max_processes=8):
        if not isinstance(max_processes, int) or isinstance(max_processes, bool) or max_processes < 1:
            raise ValueError("max_processes must be a positive integer")
        self.recipes = dict(recipes)
        if any(not isinstance(key, str) or not key or not callable(value)
               for key, value in self.recipes.items()):
            raise ValueError("recipes must map nonempty names to local callables")
        self.max_processes = max_processes
        self.processes = {}
        self._finished = OrderedDict()
        self._relays = set()
        self._lock = threading.Lock()
        self._closed = False

    def open(self, name):
        with self._lock:
            if self._closed:
                raise RuntimeError("process host is closed")
            if name not in self.recipes:
                raise ValueError("unknown host process recipe")
            if len(self.processes) >= self.max_processes:
                raise RuntimeError("host process limit reached")
            process = LocalProcess(self.recipes[name])
            handle = uuid4().hex
            self.processes[handle] = process
            return handle, process

    def control(self, handle, action, **payload):
        if action == "signal":
            number, group = payload.get("signal"), payload.get("group")
            if (set(payload) != {"signal", "group"} or type(number) is not int
                    or number not in {signal.SIGUSR1, signal.SIGTERM, signal.SIGKILL}
                    or type(group) is not bool):
                raise ValueError("unsupported host process signal")
        with self._lock:
            process = self.processes.get(handle)
            finished = self._finished.get(handle)
        if process is None:
            if finished is not None:
                if action in {"poll", "close"} and not payload:
                    return finished
                if action == "signal":
                    return None  # Like a local signal after exit; retirement can race EOF.
            raise ValueError("unknown host process handle")
        if action == "poll" and not payload:
            return process.poll()
        if action == "signal":
            process.send_signal(number, group=group)
            return None
        if action == "close" and not payload:
            return self.retire(handle)
        raise ValueError("unsupported host process operation")

    def retire(self, handle):
        with self._lock:
            process = self.processes.get(handle)
        if process is None:
            with self._lock:
                return self._finished.get(handle)
        result = process.close()
        with self._lock:
            self.processes.pop(handle, None)
            self._finished[handle] = result
            while len(self._finished) > 128:
                self._finished.popitem(last=False)
        return result

    async def relay(self, handle, process, reader, writer):
        """Tunnel raw bytes with backpressure; EOF in either direction ends ownership."""
        process.socket.setblocking(False)
        local_reader, local_writer = await asyncio.open_connection(sock=process.socket)

        async def copy(source, destination):
            while data := await source.read(64 * 1024):
                destination.write(data)
                await destination.drain()

        owner = asyncio.current_task()
        self._relays.add(owner)
        tasks = [asyncio.create_task(copy(reader, local_writer)),
                 asyncio.create_task(copy(local_reader, writer))]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self._relays.discard(owner)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            local_writer.close()
            with suppress(ConnectionError, OSError):
                await local_writer.wait_closed()
            await asyncio.to_thread(self.retire, handle)

    async def close(self):
        with self._lock:
            self._closed = True
            handles = tuple(self.processes)
        # Close asyncio transports in their owning loop before retiring raw
        # descriptors; closing a socket from a thread alone need not wake read().
        relays = tuple(self._relays)
        for task in relays:
            task.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
        await asyncio.gather(*(asyncio.to_thread(self.retire, handle) for handle in handles))
