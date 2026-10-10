import json
import os

import pytest

from the_framework.server.runtime.discovery import (
    DEFAULT_SERVER_PORT,
    PersistentSecretStore,
    ServerAlreadyRunning,
    ServerDescriptorStore,
    ServerLease,
    descriptor_path,
)


def test_installed_server_port_is_stable_and_unprivileged():
    assert DEFAULT_SERVER_PORT == 48_887


def test_browser_session_secret_is_private_and_stable(tmp_path):
    path = tmp_path / "runtime" / "ui-session.key"
    first = PersistentSecretStore(path).load_or_create()
    second = PersistentSecretStore(path).load_or_create()

    assert first == second
    assert len(first) >= 32
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_descriptor_path_is_private_runtime_state(tmp_path):
    assert descriptor_path(tmp_path / "session.json") == (
        tmp_path / "runtime" / "server.json"
    )


def test_server_descriptor_is_atomic_private_and_validated(tmp_path):
    store = ServerDescriptorStore(tmp_path / "runtime" / "server.json")
    payload = store.payload(host="127.0.0.1", port=43210, token="secret")

    store.write(payload)

    assert store.read() == payload
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert os.stat(store.path.parent).st_mode & 0o777 == 0o700
    assert store.remove_if_owned({**payload, "token": "other"}) is False
    assert store.path.exists()
    assert store.remove_if_owned(payload) is True
    assert not store.path.exists()


def test_server_descriptor_rejects_non_loopback_payload(tmp_path):
    store = ServerDescriptorStore(tmp_path / "server.json")
    store.path.write_text(json.dumps({
        "version": 1,
        "host": "0.0.0.0",
        "port": 1234,
        "token": "secret",
    }))

    with pytest.raises(ValueError, match="loopback"):
        store.read()


@pytest.mark.parametrize("value", [None, [], "descriptor", 1, True])
def test_server_descriptor_rejects_non_objects_without_removing_them(tmp_path, value):
    store = ServerDescriptorStore(tmp_path / "server.json")
    document = json.dumps(value)
    store.path.write_text(document)
    with pytest.raises(ValueError, match="must be an object"):
        store.read()
    assert store.remove_if_owned({"pid": os.getpid(), "token": "secret"}) is False
    assert store.path.read_text() == document


@pytest.mark.parametrize("port", [True, False, 0, 65536, "1234", None])
def test_server_descriptor_rejects_invalid_ports(tmp_path, port):
    store = ServerDescriptorStore(tmp_path / "server.json")
    store.write({**store.payload(host="127.0.0.1", port=1234, token="secret"), "port": port})
    with pytest.raises(ValueError, match="invalid port"):
        store.read()


def test_server_lease_rejects_a_second_runtime_owner(tmp_path):
    first = ServerLease(tmp_path / "server.lock").acquire()
    try:
        with pytest.raises(ServerAlreadyRunning):
            ServerLease(tmp_path / "server.lock").acquire()
    finally:
        first.release()

    second = ServerLease(tmp_path / "server.lock").acquire()
    second.release()
