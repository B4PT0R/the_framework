"""Efficient range-capable responses for large local media files."""

from starlette.responses import FileResponse


class MediaFileResponse(FileResponse):
    """File response tuned for sustained HD video delivery.

    Starlette's conservative 64 KiB default is appropriate for ordinary
    downloads, but it turns a large video range into thousands of threadpool
    reads and ASGI messages. One MiB keeps cancellation and memory use bounded
    while substantially reducing per-chunk overhead.
    """

    chunk_size = 1024 * 1024
