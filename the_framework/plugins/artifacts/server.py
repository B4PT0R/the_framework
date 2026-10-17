"""Asset bytes only; conversation order and replay remain owned by the session."""

import asyncio
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from ...server.api.bucket import FileBucket
from ...server.api.uploads import copy_uploaded_files, safe_filename
from ...server.composition.application import ServerExtension


class ArtifactAssets(FileBucket):
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
            "src": self.url(copied.path),
            "name": name, "mime_type": mime_type, "size": copied.size,
        }


def build_server_extensions(context):
    root = context.require("data_root")

    def build(runtime):
        assets = ArtifactAssets(root)
        runtime.application.register("artifacts", assets.handle)
        return assets

    return ServerExtension(name="artifacts", service_factory=build, endpoints="service")
