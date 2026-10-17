import os

import pytest

from the_framework.server import FileResetTransaction


def test_reset_restores_files_directories_and_removes_new_selected_data(tmp_path):
    (tmp_path / "history.json").write_text("original")
    (tmp_path / "index").mkdir()
    (tmp_path / "index" / "document").write_text("indexed")
    (tmp_path / "settings").write_text("preserved")
    transaction = FileResetTransaction(tmp_path, ["history.json", "index", "new-cache"]).stage()
    (tmp_path / "history.json").write_text("replacement")
    (tmp_path / "new-cache").write_text("new")
    transaction.restore(remove_new=True)
    assert (tmp_path / "history.json").read_text() == "original"
    assert (tmp_path / "index" / "document").read_text() == "indexed"
    assert (tmp_path / "settings").read_text() == "preserved"
    assert not (tmp_path / "new-cache").exists()
    assert not transaction.backup.exists()


def test_reset_failure_rolls_back_already_moved_files(tmp_path, monkeypatch):
    for name in ("first", "second"):
        (tmp_path / name).write_text(name)
    replace = os.replace

    def fail_second(source, destination):
        if source == tmp_path / "second":
            raise OSError("cannot move")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="cannot move"):
        FileResetTransaction(tmp_path, ["first", "second"]).stage()
    assert (tmp_path / "first").read_text() == "first"
    assert (tmp_path / "second").read_text() == "second"
    assert not list(tmp_path.glob(".session-reset-*"))


@pytest.mark.parametrize("paths", [["."], ["../outside"], ["/absolute"], ["a", "a/b"], ["a", "a"]])
def test_reset_rejects_escaping_and_overlapping_paths(tmp_path, paths):
    with pytest.raises(ValueError):
        FileResetTransaction(tmp_path, paths)


def test_reset_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_text("preserved")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        FileResetTransaction(root, ["link/data"]).stage()
    FileResetTransaction(root, ["link"]).stage().commit()
    assert (outside / "data").read_text() == "preserved"
    assert not (root / "link").exists()
