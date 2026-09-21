from collections.abc import Mapping

from modict import modict

from ...utils.tokens import DEFAULT_MAX_TOOL_OUTPUT_TOKENS
from .base import Base


class Config(Base):
    pass


class ReasoningConfig(Base):
    effort: str = "medium"
    summary: str | None = None
    generate_summary: str | None = None


class TextConfig(Base):
    format: object | None = None
    verbosity: str = "medium"


class StreamOptions(Base):
    include_obfuscation: bool | None = None


class PromptConfig(Base):
    id: str
    variables: dict | None = None
    version: str | None = None


class ContextManagementConfig(Base):
    type: str
    compact_threshold: int | None = None


class CompactionConfig(Base):
    model: str = "gpt-5.6-luna"
    instructions: str | None = None
    reasoning: ReasoningConfig | None = modict.factory(
        lambda: ReasoningConfig(effort="high")
    )
    text: TextConfig | None = modict.factory(TextConfig)
    prompt_cache_key: str | None = None
    context_token_limit: int = 200_000
    trigger_ratio: float = 0.95
    archive_anchor_limit: int = 10


class AgentConfig(Config):
    background: bool | None = None
    context_management: list[ContextManagementConfig] | None = None
    conversation: object | None = None
    include: list | None = None
    model: str = "gpt-5.4"
    instructions: str | None = None
    max_output_tokens: int | None = None
    max_input_images: int = 15
    max_tool_output_tokens: int = DEFAULT_MAX_TOOL_OUTPUT_TOKENS
    max_tool_calls: int | None = None
    metadata: dict | None = None
    parallel_tool_calls: bool = True
    previous_response_id: str | None = None
    prompt: PromptConfig | None = None
    prompt_cache_key: str | None = None
    prompt_cache_retention: str | None = None
    reasoning: ReasoningConfig | None = None
    safety_identifier: str | None = None
    service_tier: str | None = None
    store: bool | None = False
    stream_options: StreamOptions | None = None
    temperature: float | None = None
    text: TextConfig | None = None
    tool_choice: object | None = "auto"
    top_logprobs: int | None = None
    top_p: float | None = None
    truncation: str | None = None
    user: str | None = None
    compaction: CompactionConfig = modict.factory(CompactionConfig)

    @modict.validator("max_input_images", mode="after")
    def validate_max_input_images(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("max_input_images must be a non-negative integer")
        return value

    @modict.validator("max_tool_output_tokens", mode="after")
    def validate_max_tool_output_tokens(self, value):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("max_tool_output_tokens must be a positive integer")
        return value


class Configs(AgentConfig):
    def add(self, name, config_cls=Config):
        if not isinstance(config_cls, type) or not issubclass(config_cls, Config):
            raise TypeError("plugin Config must be a Config subclass")
        if name in AgentConfig():
            raise ValueError(f"plugin config conflicts with root config: {name}")
        overrides = self.get(name, {})
        if not isinstance(overrides, Mapping):
            raise TypeError(f"plugin config must be a mapping: {name}")
        config = config_cls()
        config.merge(overrides)
        self[name] = config
        return config

    def root(self):
        return AgentConfig(**{
            name: self[name]
            for name in AgentConfig()
            if name in self
        })
