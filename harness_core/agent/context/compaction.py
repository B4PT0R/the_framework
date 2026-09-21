from ..models.usage import ResponseUsage
from ..models.lifecycle import AgentCompactionEnd, AgentCompactionStart
from .caching import resolve_prompt_cache_key
from ..models.responses import ResponseItem
from ..models.base import Base


class CompactionResponse(Base):
    id: str
    object: str = "response.compaction"
    created_at: int | None = None
    output: list[ResponseItem]
    usage: ResponseUsage | None = None

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return cls(
            **{
                **payload,
                "output": [
                    ResponseItem.from_dict(item)
                    for item in payload.get("output", [])
                ],
                "usage": (
                    ResponseUsage.from_dict(payload["usage"])
                    if payload.get("usage") is not None
                    else None
                ),
            }
        )


class Compaction:
    def __init__(self, agent):
        self.agent = agent

    def run(self, input=None):
        config = self.agent.configs.compaction.exclude(
            "context_token_limit",
            "trigger_ratio",
            "archive_anchor_limit",
        )
        config["prompt_cache_key"] = resolve_prompt_cache_key(
            self.agent.session.id,
            config.get("prompt_cache_key"),
        )
        source_input = self.agent.session.history if input is None else input
        context = getattr(self.agent, "context", None)
        context_items = (
            context.compose_session_items(source_input)
            if context is not None
            else source_input
        )
        input = self.agent.session.to_api_format(
            items=context_items,
            max_input_images=self.agent.configs.max_input_images,
        )
        retired_anchor_id = self.agent.session.anchor_id
        retired_items = (
            list(self.agent.session.history[:self.agent.session.tail_start])
            if retired_anchor_id is not None and source_input is self.agent.session.history
            else []
        )
        # This is a lifecycle notification only. The compaction input can
        # contain the complete canonical context, including encoded images,
        # and must not be copied into the worker event stream.
        self.agent.emit(AgentCompactionStart())
        response = CompactionResponse.from_dict(
            self.agent.client.responses.compact(
                input=input,
                **config,
            )
        )
        self.agent.session.commit_compaction(
            response.output,
            anchor_id=response.id,
            archive_anchor_limit=self.agent.configs.compaction.archive_anchor_limit,
            anchor_token_count=(
                response.usage.output_tokens
                if response.usage is not None
                else None
            ),
        )
        self.agent.emit(AgentCompactionEnd(
            response=response,
            retired_anchor_id=retired_anchor_id,
            retired_items=retired_items,
        ))
        return response
