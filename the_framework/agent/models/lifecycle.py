from typing import Literal

from modict import modict
from .usage import ResponseUsage

from .events import Event
from .responses import Artifact, ResponseItem


class LocalEvent(Event):
    pass


class AgentTurnStart(LocalEvent):
    type: str = "agent.turn.start"
    prompt: str | None = None


class AgentTurnEnd(LocalEvent):
    type: str = "agent.turn.end"


class AgentStepStart(LocalEvent):
    type: str = "agent.step.start"


class AgentStepEnd(LocalEvent):
    type: str = "agent.step.end"


class AgentGenerationStart(LocalEvent):
    type: str = "agent.generation.start"


class AgentGenerationEnd(LocalEvent):
    type: str = "agent.generation.end"
    status: Literal["completed", "incomplete", "interrupted", "failed"] = "completed"
    usage: ResponseUsage | None = None
    error: str | None = None


class AgentResponseItemAdded(LocalEvent):
    type: str = "agent.response_item.added"
    item: ResponseItem

    @modict.validator("item", mode="before")
    def reconstruct_item(self, value):
        return value if isinstance(value, ResponseItem) else ResponseItem.from_dict(dict(value))


class AgentProviderArtifactsUpdated(LocalEvent):
    """Replace the current turn's provider presentations without writing history."""

    type: str = "agent.provider_artifacts.updated"
    items: list[Artifact] = modict.factory(list)


class AgentCompactionStart(LocalEvent):
    type: str = "agent.compaction.start"


class AgentCompactionEnd(LocalEvent):
    type: str = "agent.compaction.end"
    response: object
    retired_anchor_id: str | None = None
    retired_items: list[ResponseItem] = modict.factory(list)

    @modict.validator("retired_items", mode="before")
    def reconstruct_retired_items(self, values):
        return [
            item if isinstance(item, ResponseItem) else ResponseItem.from_dict(dict(item))
            for item in values
        ]


class AgentTaskRequested(LocalEvent):
    type: str = "agent.task.requested"
    request_id: str
    profile: str
    prompt: str
    source_item_ids: list[str] = modict.factory(list)


class AgentToolCallsStart(LocalEvent):
    type: str = "agent.tool_calls.start"
    tool_calls: list


class AgentToolCallsEnd(LocalEvent):
    type: str = "agent.tool_calls.end"


class AgentToolCallStart(LocalEvent):
    type: str = "agent.tool_call.start"
    tool_call: object
    user_feedback: str | None = None


class AgentToolCallEnd(LocalEvent):
    type: str = "agent.tool_call.end"
    tool_call: object
    user_feedback: str | None = None
    output: object | None = None


class AgentInterrupted(LocalEvent):
    type: str = "agent.interrupted"
    reason: str | None = None


class AgentError(LocalEvent):
    type: str = "agent.error"
    error: object


class AgentOutputItemStarted(LocalEvent):
    """Provisional output identity/phase; only response_item.added commits it."""
    type: str = "agent.output_item.started"
    item: ResponseItem

    @modict.validator("item", mode="before")
    def reconstruct_item(self, value):
        return value if isinstance(value, ResponseItem) else ResponseItem.from_dict(dict(value))


class AgentTextDelta(LocalEvent):
    type: str = "agent.text.delta"
    item_id: str
    kind: str = "message"
    channel: Literal["output", "refusal", "reasoning", "summary"] = "output"
    part_index: int = 0
    delta: str
    sequence: int


class AgentTextDone(LocalEvent):
    type: str = "agent.text.done"
    item_id: str
    kind: str = "message"
    channel: Literal["output", "refusal", "reasoning", "summary"] = "output"
    part_index: int = 0
    text: str
    sequence: int


class AgentToolInputDelta(LocalEvent):
    type: str = "agent.tool_input.delta"
    item_id: str
    delta: str
    sequence: int


class AgentToolInputDone(LocalEvent):
    type: str = "agent.tool_input.done"
    item_id: str
    text: str
    sequence: int


class AgentRemoteToolStatus(LocalEvent):
    """Execution by the inference service, distinct from local tool execution."""
    type: str = "agent.remote_tool.status"
    item_id: str
    tool: str
    status: Literal["started", "running", "completed", "failed"]


class AgentTaskCompleted(LocalEvent):
    type: str = "agent.task.completed"


class AgentTaskFailed(LocalEvent):
    type: str = "agent.task.failed"
