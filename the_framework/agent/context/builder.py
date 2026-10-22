import json

from ..models.responses import Artifact, Image, Message, ResponseItem
from ..models.lifecycle import AgentCompactionEnd, AgentCompactionStart
from .caching import resolve_prompt_cache_key
from .compaction import CompactionResponse
from ...utils.tokens import token_count_payload
from ..models.usage import ResponseUsage


class Context:
    def __init__(self, agent):
        self.agent = agent
        self.last_local_input_tokens = 0

    def get_context_prefix(self):
        return self.agent.registry("hooks").run("context_prefix", [])

    def compose_session_items(self):
        """Repair canonical history before composing its dynamic context prefix."""
        session = self.agent.session
        session.sanitize()
        history = list(session.history)
        return [*self.get_context_prefix(), *history]

    @staticmethod
    def project(items, *, max_input_images=0, ephemeral=False):
        """Convert items to Responses input without mutating or persisting them."""
        if not isinstance(max_input_images, int) or max_input_images < 0:
            raise ValueError("max_input_images must be a non-negative integer")
        source = list(items)
        retained = {
            position
            for position in [
                index
                for index, item in enumerate(source)
                if isinstance(item, Image)
            ][-max_input_images:]
        } if max_input_images else set()
        projected = []
        for index, item in enumerate(source):
            if isinstance(item, Image):
                if index in retained:
                    projected.append(item.to_api_format())
                continue
            if isinstance(item, Message):
                payload = json.loads(json.dumps(item))
                projected.append({
                    "type": "message",
                    "role": item.role,
                    "content": payload.get("content", []),
                })
                continue
            if isinstance(item, Artifact):
                projected.append(item.to_api_format(ephemeral=ephemeral))
                continue
            projected.append(
                json.loads(json.dumps(item))
                if isinstance(item, ResponseItem)
                else item
            )
        return projected

    def _session_input(self):
        return self.project(
            self.compose_session_items(),
            max_input_images=self.agent.configs.max_input_images,
        )

    def _input(self, provider_outputs):
        return [
            *self._session_input(),
            *self.project(
                provider_outputs,
                max_input_images=sum(isinstance(item, Image) for item in provider_outputs),
                ephemeral=True,
            ),
        ]

    def input(self):
        provider_outputs = self.agent.registry("providers").outputs(channel="text")
        return self._input(provider_outputs)

    async def ainput(self, provider_outputs=None):
        if provider_outputs is None:
            provider_outputs = await self.agent.registry("providers").aoutputs(
                channel="text"
            )
        return self._input(provider_outputs)

    def instructions(self):
        return self.agent.registry("instructions").render(scope="agentic")

    @property
    def usage(self):
        return self.agent.session.context_usage

    def record_usage(self, usage):
        usage = ResponseUsage.from_dict(usage) if usage is not None else None
        self.agent.session.set_context_usage(usage)
        return usage

    def record_response_usage(self, response):
        if hasattr(response, "to_dict"):
            response = response.to_dict()
        elif hasattr(response, "model_dump"):
            response = response.model_dump()
        usage = response.get("usage") if isinstance(response, dict) else None
        return self.record_usage(usage)

    def local_token_payload(self, payload):
        def sanitize(value):
            if isinstance(value, list):
                return [sanitize(item) for item in value]
            if isinstance(value, dict):
                if value.get("type") == "input_image":
                    return {**value, "image_url": "<image>"}
                if (
                    value.get("type") == "compaction_summary"
                    and value.get("encrypted_content")
                ):
                    return {
                        **value,
                        "encrypted_content": "<encrypted_compaction_summary>",
                    }
                if value.get("type") == "message" and value.get("role"):
                    return {
                        "type": "message",
                        "role": value["role"],
                        "content": sanitize(value.get("content", [])),
                    }
                return {key: sanitize(item) for key, item in value.items()}
            return value

        counted = {
            key: payload[key]
            for key in (
                "instructions", "input", "tools", "tool_choice", "reasoning", "text",
            )
            if key in payload and payload[key] is not None
        }
        return sanitize(counted)

    def local_input_tokens(self, payload):
        projected = self.local_token_payload(payload)
        count = token_count_payload(projected, model=self.agent.configs.model)
        input_items = projected.get("input", [])
        if (
            self.agent.session.anchor_token_count is not None
            and any(
                isinstance(item, dict) and item.get("type") == "compaction_summary"
                for item in input_items
            )
        ):
            count += self.agent.session.anchor_token_count
        self.last_local_input_tokens = count
        return count

    def needs_compaction(self, payload):
        config = self.agent.configs.compaction
        limit = config.context_token_limit
        ratio = config.trigger_ratio
        if limit <= 0:
            raise ValueError("compaction.context_token_limit must be positive")
        if not 0 < ratio <= 1:
            raise ValueError("compaction.trigger_ratio must be greater than 0 and at most 1")
        return self.local_input_tokens(payload) >= limit * ratio

    def compact(self):
        """Compact this agent's canonical context and commit it atomically."""
        config = self.agent.configs.compaction.exclude(
            "context_token_limit",
            "trigger_ratio",
            "archive_anchor_limit",
        )
        config["prompt_cache_key"] = resolve_prompt_cache_key(
            self.agent.session.id,
            config.get("prompt_cache_key"),
        )
        input_items = self._session_input()
        retired_anchor_id = self.agent.session.anchor_id
        retired_items = (
            list(self.agent.session.history[:self.agent.session.tail_start])
            if retired_anchor_id is not None
            else []
        )
        # This is a lifecycle notification only. The compaction input can
        # contain the complete canonical context, including encoded images,
        # and must not be copied into the worker event stream.
        self.agent.emit(AgentCompactionStart())
        response = CompactionResponse.from_dict(
            self.agent.client.responses.compact(
                input=input_items,
                # Hosted-tool history needs its tool definitions during
                # compaction just as it does during ordinary generation.
                tools=self.agent.registry("tools").list(),
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
