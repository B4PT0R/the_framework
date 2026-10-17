"""One authenticated file tree for client-visible application resources."""

from pathlib import Path
from urllib.parse import quote

from .declarations import endpoint
from .endpoints import HttpError
from .media import MediaFileResponse


class FileBucket:
    def __init__(self, data_root):
        self.data_root = Path(data_root).expanduser().resolve()
        self.root = self.data_root / "bucket"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def url(self, path):
        relative = Path(path).resolve().relative_to(self.root)
        return f"/api/v1/bucket/{quote(relative.as_posix(), safe='/')}"

    def response(self, path):
        source = Path(path).resolve()
        if not source.is_relative_to(self.root) or not source.is_file():
            raise HttpError(404, "file_unavailable", "File is unavailable")
        return MediaFileResponse(
            source, filename=source.name, content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    @endpoint("get", "/api/v1/bucket/{path:path}",
              request={"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"], "additionalProperties": False},
              authorization={"scope": "session:read"})
    async def content(self, path: str):
        return self.response(self.root / path)
