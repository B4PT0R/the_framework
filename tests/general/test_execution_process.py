"""Execution channels own their process lifetime, not conversation state."""

import asyncio
import os
import signal
import socket
import subprocess
import sys

import pytest

from the_framework.execution import LocalProcess, ProcessHost, RemoteProcess

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX inherited process channels")


def echo_recipe(fd):
    return [sys.executable, "-c", (
        "import socket,sys; s=socket.socket(fileno=int(sys.argv[1])); "
        "data=s.recv(65536); s.sendall(data[::-1])"
    ), str(fd)]


def idle_recipe(fd):
    return [sys.executable, "-c", "import time; time.sleep(60)"]


def test_local_channel_roundtrip_exit_and_reap():
    process = LocalProcess(echo_recipe)
    try:
        process.socket.settimeout(5)
        process.socket.sendall(b"channel test")
        assert process.socket.recv(64) == b"tset lennahc"
        assert process.wait(timeout=5) == 0
    finally:
        process.close()
    assert process.poll() == 0
    assert process.socket.fileno() == -1


def test_local_control_timeout_and_group_cleanup():
    process = LocalProcess(idle_recipe)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.wait(timeout=.01)
        assert process.poll() is None
        process.send_signal(signal.SIGTERM, group=True)
        assert process.wait(timeout=5) == -signal.SIGTERM
    finally:
        process.close()


def test_host_recipes_limits_validation_and_close():
    async def scenario():
        host = ProcessHost({"idle": idle_recipe}, max_processes=1)
        with pytest.raises(ValueError, match="unknown .* recipe"):
            host.open("arbitrary.module")
        handle, process = host.open("idle")
        try:
            with pytest.raises(RuntimeError, match="limit"):
                host.open("idle")
            assert host.control(handle, "poll") is None
            for payload in ({"signal": 0, "group": True},
                            {"signal": signal.SIGKILL, "group": 1},
                            {"signal": "9", "group": False}):
                with pytest.raises(ValueError):
                    host.control(handle, "signal", **payload)
            with pytest.raises(ValueError):
                host.control(handle, "poll", command="anything")
        finally:
            await host.close()
        assert not host.processes
        assert process.poll() is not None
        with pytest.raises(RuntimeError, match="closed"):
            host.open("idle")
    asyncio.run(scenario())


def test_host_relay_transfers_bytes_and_retires_on_disconnect():
    async def scenario():
        host = ProcessHost({"echo": echo_recipe, "idle": idle_recipe})
        ownership = []
        tasks = set()

        async def connect(reader, writer):
            handle, process = host.open((await reader.readline()).decode().strip())
            ownership.append(process)
            try:
                await host.relay(handle, process, reader, writer)
            finally:
                writer.close()
                await writer.wait_closed()

        def connected(reader, writer):
            task = asyncio.create_task(connect(reader, writer))
            tasks.add(task)

        server = await asyncio.start_server(connected, "127.0.0.1", 0)
        address = server.sockets[0].getsockname()[:2]
        try:
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"echo\nabc")
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(3), 5) == b"cba"
            assert await asyncio.wait_for(reader.read(), 5) == b""
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_connection(*address)
            writer.write(b"idle\n")
            await writer.drain()
            while len(ownership) < 2:
                await asyncio.sleep(.01)
            writer.close()
            await writer.wait_closed()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert not host.processes
            assert all(process.poll() is not None for process in ownership)
        finally:
            server.close()
            await server.wait_closed()
            await host.close()
    asyncio.run(scenario())


def test_retired_status_is_real_and_unknown_handles_fail():
    host = ProcessHost({"echo": echo_recipe})
    handle, process = host.open("echo")
    process.socket.sendall(b"x")
    assert process.socket.recv(1) == b"x"
    assert process.wait(timeout=5) == 0
    assert host.retire(handle) == 0
    assert host.control(handle, "poll") == 0
    assert host.control(handle, "close") == 0
    assert host.control(handle, "signal", signal=int(signal.SIGKILL), group=True) is None
    with pytest.raises(ValueError, match="unsupported .* signal"):
        host.control(handle, "signal", signal=0, group=True)
    with pytest.raises(ValueError, match="unknown .* handle"):
        host.control("expired", "poll")


def test_local_close_delivers_eof_and_allows_child_cleanup():
    def recipe(fd):
        return [sys.executable, "-c", r"""
import socket, subprocess, sys
s = socket.socket(fileno=int(sys.argv[1]))
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                         start_new_session=True)
s.sendall(str(child.pid).encode() + b'\n')
while s.recv(1024):
    pass
child.terminate()
child.wait(timeout=2)
""", str(fd)]
    process = LocalProcess(recipe)
    process.socket.settimeout(5)
    pid = int(process.socket.recv(100))
    assert process.close() == 0
    assert process.close() == 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_remote_close_keeps_actual_status_and_releases_on_failure():
    class Endpoint:
        def open_process(self, name):
            self.socket, self.peer = socket.socketpair()
            return self.socket, "handle"

        def process_control(self, handle, action):
            if action == "close":
                if self.fail:
                    raise ConnectionError("host lost")
                return 7
            return None

    for fail in (False, True):
        endpoint = Endpoint()
        endpoint.fail = fail
        process = RemoteProcess(endpoint, "test")
        try:
            if fail:
                with pytest.raises(ConnectionError):
                    process.close()
                with pytest.raises(RuntimeError, match="unavailable"):
                    process.poll()
            else:
                assert process.poll() is None
                process.close()
                assert process.poll() == 7
                assert process.wait(timeout=0) == 7
            assert process.socket.fileno() == -1
            process.close()
        finally:
            endpoint.peer.close()
