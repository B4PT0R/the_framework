from ..models.responses import Compaction, Image
from ...utils.tokens import token_count_payload
from ..models.usage import ResponseUsage


class Context:
    def __init__(self, agent):
        self.agent = agent
        self.last_local_input_tokens = 0

    def get_context_prefix(self):
        return self.agent.registry("hooks").run("context_prefix", [])

    def compose_session_items(self, items=None):
        """Place dynamic prefix items at the head of the active context epoch."""
        session = self.agent.session
        canonical = items is None or items is session.history
        if canonical:
            session.sanitize()
        history = list(session.history if canonical else items)
        prefix = list(self.get_context_prefix())
        if not prefix:
            return history

        if canonical and session.anchor_id is not None and session.tail_start:
            compacted_output = history[:session.tail_start]
            if any(isinstance(item, Compaction) for item in compacted_output):
                return [
                    *prefix,
                    *compacted_output,
                    *history[session.tail_start:],
                ]
        return [*prefix, *history]

    def _input(self, provider_outputs):
        provider_image_count = sum(
            isinstance(item, Image) for item in provider_outputs
        )
        return [
            *self.agent.session.to_api_format(
                max_input_images=self.agent.configs.max_input_images,
                items=self.compose_session_items(),
            ),
            *self.agent.session.to_api_format(
                max_input_images=provider_image_count,
                items=provider_outputs,
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
        usage = ResponseUsage.from_dict(usage)
        self.agent.session.set_context_usage(usage)
        return usage

    def record_response_usage(self, response):
        if hasattr(response, "to_dict"):
            response = response.to_dict()
        elif hasattr(response, "model_dump"):
            response = response.model_dump()
        usage = response.get("usage") if isinstance(response, dict) else None
        return self.record_usage(usage) if usage is not None else None

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
