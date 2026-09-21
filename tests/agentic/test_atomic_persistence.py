import asyncio
import os
import stat

import pytest

from core.utils import persistence


def test_atomic_write_is_private_and_invisible_until_committed(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text("old")
    with persistence.atomic_text_writer(path) as stream:
        stream.write("new é")
        assert path.read_text() == "old"
        assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
    assert path.read_text() == "new é"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("error", [ValueError, asyncio.CancelledError])
def test_failed_write_preserves_snapshot_and_removes_temporary(tmp_path, error):
    path = tmp_path / "snapshot.json"
    path.write_text("old")
    with pytest.raises(error):
        with persistence.atomic_text_writer(path) as stream:
            stream.write("partial")
            raise error()
    assert path.read_text() == "old"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_commit_failure_preserves_previous_snapshot(tmp_path, monkeypatch, operation):
    path = tmp_path / "snapshot.json"
    path.write_text("old")

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(persistence.os, operation, fail)
    with pytest.raises(OSError, match="disk failure"):
        with persistence.atomic_text_writer(path) as stream:
            stream.write("new")
    assert path.read_text() == "old"
    assert list(tmp_path.iterdir()) == [path]


def test_overlapping_writers_have_independent_temporaries(tmp_path):
    path = tmp_path / "snapshot.json"
    with persistence.atomic_text_writer(path) as first:
        first.write("first")
        with persistence.atomic_text_writer(path) as second:
            second.write("second")
            assert first.name != second.name
        assert path.read_text() == "second"
    assert path.read_text() == "first"
    assert list(tmp_path.iterdir()) == [path]


def test_directory_sync_failure_reports_uncertain_durability(tmp_path, monkeypatch):
    path = tmp_path / "snapshot.json"
    path.write_text("old")
    sync = os.fsync

    def fail_directory_sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory sync failed")
        sync(descriptor)

    monkeypatch.setattr(persistence.os, "fsync", fail_directory_sync)
    with pytest.raises(OSError, match="directory sync failed"):
        with persistence.atomic_text_writer(path) as stream:
            stream.write("new")
    # Replacement already happened; the primitive must not pretend to roll back.
    assert path.read_text() == "new"
    assert list(tmp_path.iterdir()) == [path]
