"""Reversible filesystem staging for a worker reset, with application-owned paths."""

from collections.abc import Iterable

import os
import shutil
from pathlib import Path
from uuid import uuid4


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


class FileResetTransaction:
    """Stage selected paths on the same filesystem; restore if worker restart fails.

    Paths are relative to root, non-overlapping, and must not traverse symlinks.
    This handles in-process rollback, not recovery from a host/process crash.
    """

    def __init__(self, root: str | Path, paths: Iterable[str | Path]):
        self.root = Path(root).resolve()
        self.paths = tuple(Path(path) for path in paths)
        for index, path in enumerate(self.paths):
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise ValueError("reset paths must be nonempty relative paths below root")
            for previous in self.paths[:index]:
                if path == previous or path in previous.parents or previous in path.parents:
                    raise ValueError("reset paths must not overlap")
        self.backup = self.root / f".session-reset-{uuid4().hex}"
        self.moved = []

    def _destination(self, path):
        for parent in path.parents:
            if (self.root / parent).is_symlink():
                raise ValueError("reset paths must not traverse symlinks")
        return self.root / path

    def stage(self):
        self.backup.mkdir(mode=0o700)
        try:
            for path in self.paths:
                source = self._destination(path)
                if not source.exists() and not source.is_symlink():
                    continue
                destination = self.backup / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                self.moved.append(path)
        except Exception:
            self.restore()
            raise
        return self

    def restore(self, *, remove_new=False):
        for path in self.paths if remove_new else self.moved:
            _remove(self._destination(path))
        for path in self.moved:
            destination = self._destination(path)
            source = self.backup / path
            if source.exists() or source.is_symlink():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
        self.moved.clear()
        _remove(self.backup)

    def commit(self):
        self.moved.clear()
        _remove(self.backup)
