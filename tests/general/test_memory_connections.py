import sqlite3

import pytest

from the_framework.plugins.memory.store import MemoryStore


def test_disk_memory_connections_close_after_success_and_failure(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    with store.connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")

    with pytest.raises(RuntimeError, match="abort"):
        with store.connect() as failed:
            failed.execute("CREATE TABLE IF NOT EXISTS transaction_probe (value INTEGER)")
            failed.execute("INSERT INTO transaction_probe VALUES (1)")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        failed.execute("SELECT 1")
    with store.connect() as following:
        assert following.execute("SELECT COUNT(*) FROM transaction_probe").fetchone()[0] == 0


def test_in_memory_store_keeps_its_owned_connection_between_operations():
    store = MemoryStore(":memory:")
    try:
        with store.connect() as first:
            first.execute("CREATE TABLE connection_probe (value INTEGER)")
            first.execute("INSERT INTO connection_probe VALUES (42)")
        with store.connect() as second:
            assert second.execute("SELECT value FROM connection_probe").fetchone()[0] == 42
        assert store.active() == []
    finally:
        store._connection.close()
