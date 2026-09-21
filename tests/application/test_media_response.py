from core.server.api.media import MediaFileResponse


def test_large_media_response_uses_megabyte_chunks():
    assert MediaFileResponse.chunk_size == 1024 * 1024
