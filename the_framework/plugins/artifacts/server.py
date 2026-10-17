"""Asset bytes only; conversation order and replay remain owned by the session."""

import asyncio
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from urllib.parse import quote

from ...server.api.declarations import endpoint
from ...server.api.endpoints import HttpError
from ...server.api.media import MediaFileResponse
from ...server.api.uploads import copy_uploaded_files, safe_filename
from ...server.composition.application import ServerExtension


class ArtifactAssets:
    def __init__(self, root):
        self.root = Path(root).resolve()

    async def handle(self, method, payload):
        if method != "publish":
            raise ValueError(f"unknown artifact operation: {method}")
        return await asyncio.to_thread(self.publish, **payload)

    def publish(self, kind, payload):
        if kind not in {"image", "file"}:
            raise ValueError("only image/file artifacts need asset publication")
        source = Path(payload["path"]).expanduser().resolve()
        if not source.is_file():
            raise ValueError("artifact path must be an existing regular file")
        name = safe_filename(source.name)
        # Unique names freeze each publication, even when a source is overwritten.
        asset = f"{uuid4().hex}-{name.encode('utf-8')[:180].decode('utf-8', errors='ignore')}"
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        with source.open("rb") as stream:
            copied = copy_uploaded_files([SimpleNamespace(
                file=stream, filename=asset, content_type=mime_type,
            )], self.root)[0]
        try:
            if kind == "image":
                from PIL import Image
                with Image.open(copied.path) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                        raise ValueError("image artifacts support PNG, JPEG, WebP and GIF")
                    mime_type = Image.MIME[image.format]
                    suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}[image.format]
                    image.verify()
                # Serve the verified raster MIME even when the source has no suffix.
                if not copied.name.lower().endswith(suffix):
                    destination = Path(copied.path + suffix)
                    Path(copied.path).rename(destination)
                    copied.path, copied.name = str(destination), destination.name
        except Exception:
            Path(copied.path).unlink(missing_ok=True)
            raise
        return {
            **{key: value for key, value in payload.items() if key not in {"path", "src"}},
            "src": f"/api/v1/artifacts/{quote(copied.name, safe='')}",
            "name": name, "mime_type": mime_type, "size": copied.size,
        }

    @endpoint("get", "/api/v1/artifacts/{asset}",
              request={"type": "object", "properties": {"asset": {"type": "string"}},
                       "required": ["asset"], "additionalProperties": False},
              authorization={"scope": "session:read"})
    async def content(self, asset: str):
        path = (self.root / asset).resolve()
        if path.parent != self.root or not path.is_file():
            raise HttpError(404, "artifact_unavailable", "Artifact file is unavailable")
        return MediaFileResponse(
            path, filename=asset.partition("-")[2] or asset,
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )


def build_server_extensions(context):
    root = Path(context.require("data_root")) / "artifacts"

    def build(runtime):
        assets = ArtifactAssets(root)
        runtime.application.register("artifacts", assets.handle)
        return assets

    return ServerExtension(name="artifacts", service_factory=build, endpoints="service")
