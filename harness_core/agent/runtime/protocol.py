from modict import modict

from ..models.worker import WorkerProfile
from ..models.events import Event
from ..models.responses import ResponseItem
from ..context.session import SessionWatermark
from ..models.base import TypedBase
from ..models.usage import UsageWindow


class WorkerRequest(TypedBase):
    id: str = modict.field(required="always")


class QueuedRequest(WorkerRequest):
    future = modict.attr(None)
    priority: int = 10
    status: str = "queued"


class PromptRequest(QueuedRequest):
    prompt: str = modict.field(required="always")
    append_prompt: bool = True
    prompt_role: str = "user"
    prompt_kind: str = "message"
    # Optional, per-command Responses override. ``None`` means inherit the
    # canonical agent configuration; "none" explicitly disables reasoning.
    reasoning_effort: str | None = None
    input_items: list[ResponseItem] = modict.factory(list)

    @modict.validator("reasoning_effort", mode="after")
    def validate_reasoning_effort(self, value):
        if value not in {None, "none", "low", "medium", "high", "xhigh"}:
            raise ValueError(
                "reasoning_effort must be none, low, medium, high, xhigh, or null"
            )
        return value

    @modict.validator("input_items", mode="before")
    def reconstruct_input_items(self, values):
        return [
            item if isinstance(item, ResponseItem) else ResponseItem.from_dict(dict(item))
            for item in values
        ]


class ExternalEventRequest(QueuedRequest):
    name: str = modict.field(required="always")
    payload: object = modict.factory(dict)


class TransientEventRequest(WorkerRequest):
    """Ephemeral runtime telemetry handled outside the durable command queue."""

    name: str = modict.field(required="always")
    payload: object = modict.factory(dict)


class InterruptRequest(WorkerRequest):
    reason: str | None = None


class StatusRequest(WorkerRequest):
    pass


class SessionSnapshotRequest(WorkerRequest):
    pass


class SessionPageRequest(WorkerRequest):
    before: str | None = None
    limit: int = 25
    projection: str = "full"


class ConfigSnapshotRequest(WorkerRequest):
    pass


class StateSnapshotRequest(WorkerRequest):
    pass


class ConfigUpdateRequest(QueuedRequest):
    updates: dict = modict.factory(dict)


class PluginBindingRequest(QueuedRequest):
    plugin: str = modict.field(required="always")
    enabled: bool = modict.field(required="always")


class CompactRequest(QueuedRequest):
    pass


class ShutdownRequest(WorkerRequest):
    pass


class ApplicationRequest(TypedBase):
    id: str = modict.field(required="always")
    capability: str = modict.field(required="always")
    method: str = modict.field(required="always")
    payload: object = modict.factory(dict)
    timeout_ms: int = 30_000


class ApplicationResult(WorkerRequest):
    status: str
    result: object | None = None
    error: str | None = None


class WorkerOutput(TypedBase):
    request_id: str | None = None


class TransientEventCompleted(WorkerOutput):
    result: object | None = None


class WorkerReady(WorkerOutput):
    protocol: int = 1
    session_id: str
    name: str = "agent"
    description: str = "General-purpose agent worker."
    specialists: list[WorkerProfile] = modict.factory(list)
    plugins: list[dict] = modict.factory(list)

class CommandAccepted(WorkerOutput):
    command: QueuedRequest

    @modict.validator("command", mode="before")
    def reconstruct_command(self, value):
        return value if isinstance(value, WorkerRequest) else WorkerRequest.from_dict(dict(value))


class CommandEvent(WorkerOutput):
    command_id: str
    event: Event
    watermark: SessionWatermark | None = None

    @modict.validator("event", mode="before")
    def reconstruct_event(self, value):
        return value if isinstance(value, Event) else Event.from_dict(dict(value))


class CommandCompleted(WorkerOutput):
    command: QueuedRequest

    @modict.validator("command", mode="before")
    def reconstruct_command(self, value):
        return value if isinstance(value, WorkerRequest) else WorkerRequest.from_dict(dict(value))


class CommandFailed(WorkerOutput):
    command: QueuedRequest
    error: str

    @modict.validator("command", mode="before")
    def reconstruct_command(self, value):
        return value if isinstance(value, WorkerRequest) else WorkerRequest.from_dict(dict(value))


class WorkerStatus(WorkerOutput):
    active: QueuedRequest | None = None
    pending: int = 0
    foreground_active: QueuedRequest | None = None
    foreground_pending: int = 0
    local_input_tokens: int = 0
    context_token_limit: int = 0
    context_saturation: float = 0.0
    quota_5h: UsageWindow | None = None
    quota_7d: UsageWindow | None = None
    quota_updated_at: int | None = None

    @modict.validator("active", mode="before")
    def reconstruct_active(self, value):
        return value if value is None or isinstance(value, WorkerRequest) else WorkerRequest.from_dict(dict(value))

    @modict.validator("foreground_active", mode="before")
    def reconstruct_foreground(self, value):
        return value if value is None or isinstance(value, WorkerRequest) else WorkerRequest.from_dict(dict(value))


class SessionSnapshot(WorkerOutput):
    watermark: SessionWatermark
    items: list[ResponseItem] = modict.factory(list)
    archive: list[ResponseItem] = modict.factory(list)
    instructions: str = ""
    extensions: dict = modict.factory(dict)

    @modict.validator("items", mode="before")
    def reconstruct_items(self, values):
        return [
            item if isinstance(item, ResponseItem) else ResponseItem.from_dict(dict(item))
            for item in values
        ]

    @modict.validator("archive", mode="before")
    def reconstruct_archive(self, values):
        return [
            item if isinstance(item, ResponseItem) else ResponseItem.from_dict(dict(item))
            for item in values
        ]


class SessionTurn(TypedBase):
    id: str
    items: list[ResponseItem] = modict.factory(list)

    @modict.validator("items", mode="before")
    def reconstruct_items(self, values):
        return [
            item if isinstance(item, ResponseItem) else ResponseItem.from_dict(dict(item))
            for item in values
        ]


class SessionPage(WorkerOutput):
    watermark: SessionWatermark
    turns: list[SessionTurn] = modict.factory(list)
    next_before: str | None = None
    has_more: bool = False

class ConfigSnapshot(WorkerOutput):
    config: dict = modict.factory(dict)
    plugins: list[dict] = modict.factory(list)


class StateSnapshot(WorkerOutput):
    state: dict = modict.factory(dict)


class AgentConfigUpdated(Event):
    type: str = "agent.config.updated"
    config: dict = modict.factory(dict)
    restart_required: bool = False


class AgentStateUpdated(Event):
    type: str = "agent.state.updated"
    state: dict = modict.factory(dict)


class PluginBindingUpdated(Event):
    type: str = "agent.plugin.binding.updated"
    plugin: str
    enabled: bool


class WorkerStopped(WorkerOutput):
    pass


class WorkerProtocolError(WorkerOutput):
    error: str


class ApplicationCall(WorkerOutput):
    command_id: str | None = None
    request: ApplicationRequest

    @modict.validator("request", mode="before")
    def reconstruct_request(self, value):
        return value if isinstance(value, ApplicationRequest) else ApplicationRequest.from_dict(dict(value))


class ApplicationCancel(WorkerOutput):
    command_id: str | None = None
    call_id: str
    reason: str
