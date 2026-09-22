import asyncio
from types import SimpleNamespace

import pytest

from the_framework.agent.runtime.protocol import SessionSnapshotRequest, StatusRequest
from the_framework.server.api.endpoints import HttpError
from starter.voice import VoiceApi
from starter.chat import ChatApi


def test_voice_requires_idle_text_and_passes_canonical_snapshot():
    async def check():
        snapshot = object()
        calls = []
        status = SimpleNamespace(foreground_active=None, foreground_pending=0)

        async def request(command):
            if isinstance(command, StatusRequest):
                return status
            assert isinstance(command, SessionSnapshotRequest)
            return snapshot

        async def start(offer, received):
            calls.append((offer, received))
            return {"answer_sdp": "answer"}

        api = VoiceApi(SimpleNamespace(request=request), SimpleNamespace(start=start, active=False))
        assert await api.start("offer", "one") == {"answer_sdp": "answer"}
        assert calls == [("offer", snapshot)]
        status.foreground_pending = 1
        with pytest.raises(HttpError):
            await api.start("second offer", "two")
        assert len(calls) == 1

    asyncio.run(check())


def test_voice_cleanup_and_readiness_are_scoped_to_the_originating_call():
    async def check():
        stopped = []

        async def stop():
            stopped.append(True)

        api = VoiceApi(None, SimpleNamespace(active=True, stop=stop))
        api.call_id = "main-window"
        with pytest.raises(HttpError):
            await api.start("offer", "preview")
        with pytest.raises(HttpError):
            await api.ready("preview")
        await api.stop("preview")
        assert stopped == []
        await api.stop("main-window")
        assert stopped == [True]
        assert api.call_id is None

    asyncio.run(check())


def test_text_joins_voice_and_attachments_do_not_start_a_parallel_turn(tmp_path):
    async def check():
        from io import BytesIO
        from starlette.datastructures import UploadFile

        submitted, spoken = [], []

        async def submit(command):
            submitted.append(command)

        async def send_text(text):
            spoken.append(text)

        runtime = SimpleNamespace(submit=submit)
        controller = SimpleNamespace(active=True, send_text=send_text)
        voice = VoiceApi(runtime, controller)
        chat = ChatApi(runtime, tmp_path, voice)
        await chat.prompt("typed", "Hello")
        assert spoken == ["Hello"]
        assert submitted == []
        file = UploadFile(filename="notes.txt", file=BytesIO(b"notes"))
        with pytest.raises(HttpError):
            await chat.attachments("files", [file])
        assert file.file.closed
        assert list(tmp_path.iterdir()) == []
        controller.active = False
        await chat.prompt("text", "Back to text")
        assert submitted[0].prompt == "Back to text"

    asyncio.run(check())


def test_text_and_attachments_work_without_voice(tmp_path):
    async def check():
        from io import BytesIO
        from starlette.datastructures import UploadFile

        submitted = []

        async def submit(command):
            submitted.append(command)

        chat = ChatApi(SimpleNamespace(submit=submit), tmp_path)
        await chat.prompt("text", "Hello without voice")
        file = UploadFile(filename="notes.txt", file=BytesIO(b"notes"))
        result = await chat.attachments("files", [file], "Read this")
        assert result["status"] == "submitted"
        assert [command.id for command in submitted] == ["text", "files"]
        assert (tmp_path / "notes.txt").read_bytes() == b"notes"

    asyncio.run(check())
