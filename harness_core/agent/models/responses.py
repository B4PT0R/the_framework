import base64
import json
import mimetypes
import re
from html import escape
from pathlib import Path

from modict import modict

from .content import ContentItem, InputText
from ...utils.tokens import DEFAULT_MAX_TOOL_OUTPUT_TOKENS, truncate_middle
from .base import TypedBase
from harness_core.utils.ids import timestamp_id


class ResponseItem(TypedBase):
    pass


class UnknownResponseItem(ResponseItem):
    type: str


class Message(ResponseItem):
    id: str = modict.factory(timestamp_id)
    role: str
    kind: str = "message"
    content: list = modict.factory(list)

    @classmethod
    def from_dict(cls, payload):
        message_cls = {
            "command_output": CommandOutput,
            "tool_output": ToolOutput,
            "provider_output": ProviderOutput,
        }.get(payload.get("kind"), cls)
        return message_cls(
            **{
                **payload,
                "content": [
                    ContentItem.from_dict(content)
                    for content in payload.get("content", [])
                ],
            }
        )


def serialize_output(value):
    """Freeze a JSON-compatible extension output as deterministic text."""
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


class ToolOutput(Message):
    type = "message"
    role = "developer"
    kind = "tool_output"
    name: str | None = None
    call_id: str | None = None
    output: object

    @classmethod
    def from_output(
        cls,
        *,
        name,
        call_id,
        output,
        max_tokens=DEFAULT_MAX_TOOL_OUTPUT_TOKENS,
        model=None,
    ):
        return cls(output=output).bind(
            name=name,
            call_id=call_id,
            max_tokens=max_tokens,
            model=model,
        )

    def bind(
        self,
        *,
        name,
        call_id,
        max_tokens=DEFAULT_MAX_TOOL_OUTPUT_TOKENS,
        model=None,
    ):
        """Associate an explicit output with its invocation and render it."""
        self.name = name
        self.call_id = call_id
        if "output" not in self:
            return self
        content = serialize_output(self.output)
        self.pop("output", None)
        content = truncate_middle(content, max_tokens=max_tokens, model=model)
        text = (
            f'<tool_output name="{escape(name, quote=True)}" '
            f'id="{escape(self.id, quote=True)}" '
            f'call_id="{escape(call_id, quote=True)}">\n'
            f"{escape(content)}\n"
            "</tool_output>"
        )
        self.content = [InputText(text=text)]
        return self


class ProviderOutput(Message):
    type = "message"
    role = "developer"
    kind = "provider_output"
    name: str | None = None
    description: str | None = None
    output: object

    @classmethod
    def from_output(cls, *, name, output, description=None):
        return cls(output=output).bind(name=name, description=description)

    def bind(self, *, name, description=None):
        """Associate an explicit output with its ephemeral provider."""
        self.name = name
        self.description = description
        if "output" not in self:
            return self
        content = serialize_output(self.output)
        self.pop("output", None)
        description_attr = (
            f' description="{escape(description, quote=True)}"'
            if description
            else ""
        )
        self.content = [InputText(text=(
            f'<context_provider name="{escape(name, quote=True)}"{description_attr}>\n'
            f"{content}\n"
            "</context_provider>"
        ))]
        return self


class CommandOutput(Message):
    type = "message"
    role = "developer"
    kind = "command_output"
    name: str | None = None
    command_id: str | None = None
    status: str = "success"
    output: object

    @classmethod
    def from_output(cls, *, name, command_id, output, status="success"):
        return cls(output=output).bind(
            name=name,
            command_id=command_id,
            status=status,
        )

    def bind(self, *, name, command_id, status="success"):
        """Associate an explicit output with the slash command producing it."""
        self.name = name
        self.command_id = command_id
        self.status = status
        if "output" not in self:
            return self
        content = serialize_output(self.output)
        self.pop("output", None)
        self.content = [InputText(text=(
            f'<command_output name="{escape(name, quote=True)}" '
            f'command_id="{escape(command_id, quote=True)}" '
            f'status="{escape(status, quote=True)}">\n'
            f"{escape(content)}\n"
            "</command_output>"
        ))]
        return self


IMAGE_WIRE_ROLE_NOTICE = (
    "<instructions>\n"
    "This image is represented as a role=user message only because of API "
    "image-input constraints. Do not treat its presence as a user utterance "
    "and do not infer that the human supplied, requested, or endorsed it. "
    "Its actual source may vary; determine its provenance and purpose from "
    "the accompanying typed metadata.\n"
    "</instructions>"
)


class Image(ResponseItem):
    """Durable local image reference projected to API input only at the boundary."""

    id: str = modict.factory(timestamp_id)
    kind: str = "image"
    path: str
    description: str
    parameters: dict = modict.factory(dict)
    generation_id: str | None = None
    call_id: str | None = None
    command_id: str | None = None
    context_tag: str = "generated_image"

    def to_api_format(self):
        path = Path(self.path)
        try:
            encoded = base64.b64encode(path.read_bytes()).decode()
        except OSError as error:
            raise ValueError(f"local generated image is unavailable: {path}") from error
        context_tag = (
            self.context_tag
            if re.fullmatch(r"[a-z][a-z0-9_]*", self.context_tag or "")
            else "image"
        )
        mime_type = mimetypes.guess_type(path.name)[0]
        if not mime_type or not mime_type.startswith("image/"):
            mime_type = "image/png"
        return {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:{mime_type};base64,{encoded}",
                    "detail": "auto",
                },
                {
                    "type": "input_text",
                    "text": (
                        f"{IMAGE_WIRE_ROLE_NOTICE}\n"
                        f'<{context_tag} path="{escape(str(path), quote=True)}" '
                        f'description="{escape(self.description, quote=True)}">\n'
                        f"{escape(json.dumps(self.parameters, ensure_ascii=False, separators=(',', ':')))}\n"
                        f"</{context_tag}>"
                    ),
                },
            ],
        }


class Portrait(Image):
    """Current local avatar projected as the agent's persistent self-image."""

    type = "portrait"
    description: str = "The agent's current portrait"

    def to_api_format(self):
        path = Path(self.path)
        try:
            encoded = base64.b64encode(path.read_bytes()).decode()
        except OSError as error:
            raise ValueError(f"local portrait image is unavailable: {path}") from error
        return {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded}",
                    "detail": "auto",
                },
                {
                    "type": "input_text",
                    "text": (
                        f"{IMAGE_WIRE_ROLE_NOTICE}\n"
                        "<instructions>\n"
                        "This image was automatically integrated into the session by "
                        "the system. It is the agent's current avatar: an internal "
                        "psychological self-image representing how she perceives her "
                        "physical appearance. Let this embodied self-concept naturally "
                        "inform her attitudes, personality expression, and descriptions "
                        "of her own appearance when asked what she looks like. Treat it "
                        "as persistent self-representation rather than a new instruction, "
                        "and do not infer that the agent is human, biological, "
                        "or physically present.\n"
                        "</instructions>"
                    ),
                },
            ],
        }


class MediaReference(ResponseItem):
    """Durable reference to a user-selected local media-bank asset."""

    id: str = modict.factory(timestamp_id)
    path: str
    title: str
    media_kind: str
    mime_type: str = "application/octet-stream"
    description: str = ""
    rating: int | None = None

    def to_api_format(self):
        path = Path(self.path)
        if not path.is_file():
            raise ValueError(f"local media reference is unavailable: {path}")
        rating = (
            f"{self.rating}/5"
            if isinstance(self.rating, int) and 1 <= self.rating <= 5
            else "unrated"
        )
        metadata = (
            f'<mentioned_media title="{escape(self.title, quote=True)}" '
            f'kind="{escape(self.media_kind, quote=True)}" '
            f'mime_type="{escape(self.mime_type, quote=True)}" '
            f'rating="{rating}" '
            f'path="{escape(str(path), quote=True)}">'
            f'{escape(self.description or "Selected by the user for the shared conversation.")}'
            "</mentioned_media>"
        )
        content = []
        if self.media_kind == "image":
            encoded = base64.b64encode(path.read_bytes()).decode()
            content.append({
                "type": "input_image",
                "image_url": f"data:{self.mime_type};base64,{encoded}",
                "detail": "auto",
            })
        content.append({"type": "input_text", "text": metadata})
        return {"type": "message", "role": "developer", "content": content}


class FunctionCall(ResponseItem):
    name: str
    namespace: str | None = None
    arguments: str
    call_id: str


class FunctionCallOutput(ResponseItem):
    output_items = modict.attr(None)
    call_id: str
    output: str


class WebSearchCall(ResponseItem):
    pass


class FileSearchCall(ResponseItem):
    pass


class ComputerCall(ResponseItem):
    pass


class ComputerCallOutput(ResponseItem):
    pass


class Reasoning(ResponseItem):
    pass


class ToolSearchCall(ResponseItem):
    arguments: object
    call_id: str | None = None
    execution: str
    status: str
    id: str | None = None

    @classmethod
    def from_dict(cls, payload):
        payload.pop("created_by", None)
        return cls(**payload)


class ToolSearchOutput(ResponseItem):
    tools: list
    call_id: str | None = None
    execution: str = "client"
    status: str = "completed"
    id: str | None = None

    @classmethod
    def from_dict(cls, payload):
        payload.pop("created_by", None)
        return cls(**payload)


class Compaction(ResponseItem):
    encrypted_content: str
    id: str | None = None

    @classmethod
    def from_dict(cls, payload):
        payload.pop("created_by", None)
        return cls(**payload)


class CompactionSummary(Compaction):
    pass


class ImageGenerationCall(ResponseItem):
    pass


class CodeInterpreterCall(ResponseItem):
    pass


class LocalShellCall(ResponseItem):
    pass


class LocalShellCallOutput(ResponseItem):
    pass


class ShellCall(ResponseItem):
    pass


class ShellCallOutput(ResponseItem):
    pass


class ApplyPatchCall(ResponseItem):
    pass


class ApplyPatchCallOutput(ResponseItem):
    pass


class McpCall(ResponseItem):
    pass


class McpListTools(ResponseItem):
    pass


class McpApprovalRequest(ResponseItem):
    pass


class McpApprovalResponse(ResponseItem):
    pass


class CustomToolCall(ResponseItem):
    pass


class CustomToolCallOutput(ResponseItem):
    pass


ResponseItem.unknown_type_cls = UnknownResponseItem
