"""Shared process ownership must survive failed startup and server replacement."""

import asyncio
import os
import signal

import pytest

from the_framework.server.runtime.discovery import ServerAlreadyRunning
from the_framework.server.runtime.local import LocalServer, listening_socket, shutdown_event


def test_replacement_rotates_control_token_but_preserves_browser_secret(tmp_path):
    path = tmp_path / "runtime/server.json"
    with LocalServer(path) as first:
        token, browser = first.token, first.browser_token
        assert first.store.read() == first.descriptor
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ServerAlreadyRunning):
            with LocalServer(path):
                pytest.fail("a duplicate server acquired the instance")
        assert first.store.read() == first.descriptor
    assert not path.exists()
    with LocalServer(path) as second:
        assert second.token != token
        assert second.browser_token == browser


def test_failed_composition_releases_discovery_socket_and_lease(tmp_path):
    path = tmp_path / "runtime/server.json"
    with pytest.raises(RuntimeError, match="composition failed"):
        with LocalServer(path) as owner:
            port = owner.descriptor["port"]
            raise RuntimeError("composition failed")
    assert not path.exists()
    with LocalServer(path, port=port):
        pass


def test_inherited_listener_remains_owned_by_supervisor(tmp_path):
    import socket

    with listening_socket() as supervisor:
        address = supervisor.getsockname()
        child = socket.socket(fileno=os.dup(supervisor.fileno()))
        with LocalServer(tmp_path / "runtime/server.json", listener=child) as owner:
            assert owner.listener.getsockname() == address
        assert child.fileno() == -1
        assert supervisor.getsockname() == address
        assert supervisor.fileno() >= 0


def test_shutdown_subscription_is_scoped_to_its_owner():
    async def check():
        previous = signal.getsignal(signal.SIGTERM)
        with shutdown_event() as stopped:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(stopped.wait(), 1)
        assert signal.getsignal(signal.SIGTERM) == previous

    asyncio.run(check())
