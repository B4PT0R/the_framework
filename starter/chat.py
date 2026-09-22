"""Optional client chat routes over the canonical agent and live voice."""

import asyncio
from pathlib import Path

from the_framework import Extension, endpoint
from the_framework.agent.models.responses import Image, Message
from the_framework.agent.runtime.protocol import PromptRequest
from the_framework.server.api.endpoints import HttpError
from the_framework.server.api.uploads import copied_files_message, copy_uploaded_files

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def prepare_attachments(files, workfolder):
    from PIL import Image as PILImage

    copied = copy_uploaded_files(files, workfolder, max_total_bytes=64 * 1024 * 1024)
    try:
        for file in copied:
            if Path(file.name).suffix.lower() in IMAGE_SUFFIXES:
                with PILImage.open(file.path) as image:
                    image.verify()
    except Exception as error:
        for file in copied:
            Path(file.path).unlink(missing_ok=True)
        raise ValueError("An attached image could not be decoded") from error
    return copied


class ChatApi:
    def __init__(self, runtime, workfolder, voice=None, *, text_lock=None):
        self.runtime = runtime
        self.workfolder = workfolder
        self.voice = voice
        self.text_lock = text_lock if text_lock is not None else asyncio.Lock()

    @endpoint("post", "/api/v1/agent/prompt", status_code=202,
              request={"type": "object", "properties": {
                  "id": {"type": "string", "minLength": 1, "maxLength": 200},
                  "prompt": {"type": "string", "minLength": 1},
              }, "required": ["id", "prompt"], "additionalProperties": False},
              response={"type": "object"})
    async def prompt(self, id: str, prompt: str):
        """Submit a client-correlated turn to the canonical single-writer queue."""
        voice = self.voice
        async with (voice.lock if voice is not None else self.text_lock):
            if voice is not None and voice.controller.active:
                await voice.controller.send_text(prompt)
            else:
                await self.runtime.submit(PromptRequest(id=id, prompt=prompt))
        return {"id": id, "status": "submitted"}

    @endpoint("post", "/api/v1/agent/attachments", status_code=202,
              request_encoding="multipart", request={"type": "object", "properties": {
                  "id": {"type": "string", "minLength": 1, "maxLength": 200},
                  "prompt": {"type": "string"},
                  "files": {"type": "array", "minItems": 1, "maxItems": 32},
              }, "required": ["id", "files"], "additionalProperties": False},
              response={"type": "object"})
    async def attachments(self, id: str, files: list, prompt: str = ""):
        """Store attachments before submitting one ordered conversation turn."""
        voice = self.voice
        async with (voice.lock if voice is not None else self.text_lock):
            if voice is not None and voice.controller.active:
                for upload in files:
                    await upload.close()
                raise HttpError(409, "voice_active", "Stop voice before sending attachments")
            return await self._attachments(id, files, prompt)

    async def _attachments(self, id, files, prompt):
        try:
            copied = await asyncio.to_thread(
                prepare_attachments, files, self.workfolder,
            )
        except ValueError as error:
            raise HttpError(422, "invalid_attachment", str(error)) from error
        finally:
            for upload in files:
                await upload.close()
        items = [Message(role="user", kind="attachments", content=[{
            "type": "input_text", "text": "Attached: " + ", ".join(file.name for file in copied),
        }]), Message(role="developer", kind="copied_files", content=[{
            "type": "input_text", "text": copied_files_message(copied, self.workfolder),
        }])]
        for file in copied:
            if Path(file.name).suffix.lower() in IMAGE_SUFFIXES:
                items.append(Image(path=file.path, description=file.name, context_tag="attachment"))
        # Acceptance may already have reached the single writer on transport failure.
        await self.runtime.submit(PromptRequest(
            id=id, prompt=prompt, append_prompt=bool(prompt.strip()), input_items=items,
        ))
        return {"id": id, "status": "submitted"}


def chat_runtime(context):
    root = Path(context.require("data_root"))
    text_mode_lock = context.require("text_mode_lock")
    return Extension(
        name="chat",
        service_factory=lambda runtime, voice=None: ChatApi(
            runtime, root / "files", voice, text_lock=text_mode_lock,
        ),
        endpoints="service",
        requires=("runtime",),
        optional_requires=("voice",),
    )
