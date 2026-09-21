"""Private, atomically replaced JSON mapping documents for application settings/state."""

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_text_writer(path):
    """Replace a private UTF-8 file durably after a successful write.

    Serialization and writer ordering belong to the caller. A failure before
    replacement preserves the old file; a directory-sync failure is propagated
    after replacement, when durability is uncertain.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class MappingStore:
    """Persist one versioned snapshot; migrations and write ordering belong to its owner."""

    version = 1
    field = "data"

    def __init__(self, path, *, field=None):
        self.path = Path(path)
        if field is not None:
            self.field = field
        if not isinstance(self.field, str) or not self.field or self.field == "version":
            raise ValueError("mapping document field must be nonempty and not 'version'")

    def load(self):
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(document, Mapping) or document.get("version") != self.version:
            raise ValueError("unsupported mapping document version")
        value = document.get(self.field)
        if not isinstance(value, Mapping):
            raise ValueError(f"document must contain a {self.field} mapping")  # noqa: TRY004 — invalid persisted value.
        return dict(value)

    def save(self, value):
        if not isinstance(value, Mapping):
            raise ValueError("document value must be a mapping")  # noqa: TRY004 — preserve settings validation contract.
        # Serialize before opening anything: malformed values cannot damage the
        # previous snapshot or leave a partially serialized temporary document.
        payload = json.dumps({"version": self.version, self.field: dict(value)}, indent=2) + "\n"
        with atomic_text_writer(self.path) as stream:
            stream.write(payload)
        return value
