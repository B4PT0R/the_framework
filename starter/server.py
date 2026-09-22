"""Assemble the worker and HTTP surface without importing the product app."""

import asyncio
import sys
from pathlib import Path

from the_framework import AgentApplication, BuildContext, Extension, endpoint
from the_framework.agent.models.responses import Image, Message
from the_framework.agent.runtime.protocol import PluginBindingRequest, PromptRequest
from the_framework.utils.ids import timestamp_id
from the_framework.server import (
    ApplicationHealthApi,
    CanonicalAgentApi,
    CanonicalTransportSockets,
    WorkerSupervisor,
    bind_application_controls,
)
from the_framework.server.api.endpoints import HttpError
from the_framework.server.api.uploads import copied_files_message, copy_uploaded_files
from the_framework.server.runtime.application import ApplicationRuntime
from the_framework.server.runtime.fleet import FleetSupervisor

from .application import ROOT, application
from .security import LocalSecurity

REFERENCE = "starter.application:application"
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
    def __init__(self, runtime, workfolder, voice):
        self.runtime = runtime
        self.workfolder = workfolder
        self.voice = voice

    @endpoint("post", "/api/v1/agent/prompt", status_code=202,
              request={"type": "object", "properties": {
                  "id": {"type": "string", "minLength": 1, "maxLength": 200},
                  "prompt": {"type": "string", "minLength": 1},
              }, "required": ["id", "prompt"], "additionalProperties": False},
              response={"type": "object"})
    async def prompt(self, id: str, prompt: str):
        """Submit a client-correlated turn to the canonical single-writer queue."""
        async with self.voice.lock:
            if self.voice.controller.active:
                await self.voice.controller.send_text(prompt)
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
        async with self.voice.lock:
            if self.voice.controller.active:
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
        # Do not remove durable files on a transport failure: acceptance may
        # already have reached the single writer.
        await self.runtime.submit(PromptRequest(
            id=id, prompt=prompt, append_prompt=bool(prompt.strip()), input_items=items,
        ))
        return {"id": id, "status": "submitted"}


def create_app(data_root, *, token, origin, runtime=None, restart=None):
    root = Path(data_root).resolve()
    if runtime is None:
        supervisor = WorkerSupervisor(root / "session.json", command=[
            sys.executable, "-m", "the_framework.agent.runtime.worker_process",
            "--session", str(root / "session.json"),
            "--application", REFERENCE, "--agent", "assistant",
        ])
        runtime = ApplicationRuntime(supervisor, fleet_factory=lambda path, profiles: FleetSupervisor(
            path, profiles, application_reference=REFERENCE,
        ))
    security = LocalSecurity(token, origin=origin)
    sockets = CanonicalTransportSockets(runtime, event_handshake=security.handshake,
                                        application_handshake=security.handshake)

    async def binding(name, enabled):
        voice = app.state.application.service("voice")
        async with voice.lock:
            if name == "realtime" and not enabled:
                await voice.controller.stop()
            result = await runtime.command(PluginBindingRequest(
                id=timestamp_id(), plugin=name, enabled=enabled,
            ))
            if result is None or result.type != "agent.plugin.binding.updated":
                raise RuntimeError("worker did not confirm plugin activation")
            if name == "scheduler":
                app.state.application.service("scheduler").active = enabled

    definition = AgentApplication({**application, "security": security}).with_extensions(
        Extension(name="runtime", service=runtime),
        Extension(name="conversation", requires=("runtime",), endpoints=(
            CanonicalAgentApi(runtime, session_projection="display"),
            ApplicationHealthApi(),
        ), websockets=(sockets.events_socket, sockets.application_socket)),
        Extension(
            name="chat",
            service_factory=lambda runtime, voice: ChatApi(runtime, root / "files", voice),
            endpoints="service",
            requires=("runtime", "voice"),
        ),
    )
    app = definition.build(BuildContext({
        "plugin_state_path": root / "plugins.json",
        "plugin_binding_update": binding,
        "surface_root": root / "surfaces",
        "surface_notify": lambda payload: runtime.publish({**payload, "type": "interface_refresh_requested"}),
        "surface_progress": runtime.publish,
        "data_root": root,
        "restart": restart,
    }))
    bind_application_controls(runtime, app, ui_root=ROOT / "ui/dist")
    app.state.runtime = runtime
    return app
