import os
import tempfile
from html import escape
from pathlib import Path

from modict import modict

MAX_FILES_PER_UPLOAD = 32
MAX_TOTAL_UPLOAD_BYTES = None
COPY_CHUNK_BYTES = 1024 * 1024


class CopiedFile(modict):
    original_name: str
    name: str
    path: str
    size: int
    content_type: str | None = None


def safe_filename(value):
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if name in {"", ".", ".."} or any(ord(character) < 32 for character in name):
        raise ValueError("uploaded files must have a valid filename")
    if len(name.encode("utf-8")) > 240:
        raise ValueError(f"uploaded filename is too long: {name[:80]}")
    return name


def _available_destination(root, name, reserved):
    source = Path(name)
    candidate = root / name
    index = 2
    while candidate.exists() or candidate in reserved:
        candidate = root / f"{source.stem} ({index}){source.suffix}"
        index += 1
    reserved.add(candidate)
    return candidate


def copy_uploaded_files(
    uploads,
    workfolder,
    *,
    max_files=MAX_FILES_PER_UPLOAD,
    max_total_bytes=MAX_TOTAL_UPLOAD_BYTES,
):
    uploads = list(uploads)
    if not uploads:
        raise ValueError("select at least one file")
    if len(uploads) > max_files:
        raise ValueError(f"select at most {max_files} files at once")

    root = Path(workfolder).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    reserved = set()
    prepared = []
    finalized = []
    total_size = 0
    try:
        for upload in uploads:
            original_name = safe_filename(getattr(upload, "filename", None))
            destination = _available_destination(root, original_name, reserved)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".incoming-",
                dir=root,
            )
            temporary = Path(temporary_name)
            size = 0
            try:
                with os.fdopen(descriptor, "wb") as target:
                    source = upload.file
                    source.seek(0)
                    while chunk := source.read(COPY_CHUNK_BYTES):
                        size += len(chunk)
                        total_size += len(chunk)
                        if max_total_bytes is not None and total_size > max_total_bytes:
                            raise ValueError(
                                "selected files exceed the combined upload limit of "
                                f"{max_total_bytes} bytes"
                            )
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.chmod(temporary, 0o600)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            prepared.append((
                temporary,
                destination,
                CopiedFile(
                    original_name=original_name,
                    name=destination.name,
                    path=str(destination),
                    size=size,
                    content_type=getattr(upload, "content_type", None),
                ),
            ))

        for temporary, destination, _record in prepared:
            # Publish without overwriting a file created by another uploader
            # after destination selection. Both paths share a filesystem.
            os.link(temporary, destination)
            finalized.append(destination)
            temporary.unlink()
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return [record for _temporary, _destination, record in prepared]
    except Exception:
        for temporary, _destination, _record in prepared:
            temporary.unlink(missing_ok=True)
        for destination in finalized:
            destination.unlink(missing_ok=True)
        raise


def copied_files_message(files, workfolder):
    files = list(files)
    lines = [
        f'<copied_files destination="{escape(str(Path(workfolder).resolve()), quote=True)}" '
        f'count="{len(files)}">'
    ]
    for file in files:
        content_type = (
            f' content_type="{escape(file.content_type, quote=True)}"'
            if file.content_type
            else ""
        )
        lines.append(
            f'  <file name="{escape(file.name, quote=True)}" '
            f'path="{escape(file.path, quote=True)}" '
            f'size_bytes="{file.size}"{content_type} />'
        )
    lines.append("</copied_files>")
    return "\n".join(lines)
