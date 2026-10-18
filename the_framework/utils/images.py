"""Prepare immutable local vision inputs before context projection."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

import httpx
from PIL import Image


MAX_IMAGE_BYTES = 50 * 1024 * 1024


def freeze_image(source, directory):
    """Read bytes, a local path or an HTTP(S) URL; validate and freeze the raster.

    Network access happens here, never during deserialization or API projection.
    HTTP errors, timeouts and invalid/oversized content fail before persistence.
    """
    if isinstance(source, bytes):
        data = source
    elif isinstance(source, str) and urlsplit(source).scheme in {"http", "https"}:
        with httpx.stream(
            "GET", source, follow_redirects=True, timeout=30,
            headers={"User-Agent": "TheFramework/0.2 (https://github.com/B4PT0R/the_framework; image retrieval)",
                     "Accept": "image/png,image/jpeg,image/webp,image/gif,image/*;q=0.8,*/*;q=0.5"},
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds the 50 MiB limit.")
                data.extend(chunk)
        data = bytes(data)
    else:
        if isinstance(source, str) and ("://" in source or source.startswith("data:")):
            raise ValueError("Image URLs must use HTTP or HTTPS.")
        with Path(source).expanduser().open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 50 MiB limit.")
    with Image.open(BytesIO(data)) as image:
        suffix = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "GIF": ".gif"}.get(image.format)
        if suffix is None:
            raise ValueError("Images support PNG, JPEG, WebP and GIF files.")
        image.verify()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (sha256(data).hexdigest() + suffix)
    if not path.exists():
        with NamedTemporaryFile(dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return path
