from modict import modict

from .events import Event
from .responses import ResponseItem


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


class AgentResponseItemAdded(LocalEvent):
    type: str = "agent.response_item.added"
    item: ResponseItem

    @modict.validator("item", mode="before")
    def reconstruct_item(self, value):
        return value if isinstance(value, ResponseItem) else ResponseItem.from_dict(dict(value))


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
