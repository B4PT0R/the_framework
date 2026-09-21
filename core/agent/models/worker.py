"""Internal JSON execution profile exchanged with supervised workers."""

from modict import modict

from .base import Base

QUEUE_POLICIES = {"fifo", "latest"}


class WorkerProfile(Base):
    """Serializable process profile compiled from a private AgentSpec."""

    name: str
    description: str | None = None
    model: str = "gpt-5.6-luna"
    instructions: str
    observes: list[str] = modict.factory(list)
    item_kinds: list[str] = modict.factory(list)
    queue_limit: int = 8
    queue_policy: str = "fifo"
    session_mode: str = "ephemeral"
    plugins: list[str] = modict.factory(list)
    agent_config: dict = modict.factory(dict)
    plugin_config: dict = modict.factory(dict)
    conversation_roles: list[str] = modict.factory(list)
    backlog_limit: int = 512
    input_item_field: str | None = None
    durable_tasks: bool = True
    idle_timeout_seconds: float | None = None
    completion_tool: str | None = None
    completion_retry_limit: int = 0

    @modict.model_validator(mode="after")
    def validate_profile(self):
        if not self.name or any(
            not part or not part.replace("_", "").isalnum()
            for part in self.name.split(".")
        ):
            raise ValueError("agent name must be a dotted Python-style identity")
        if not self.model or not self.instructions:
            raise ValueError("specialist model and instructions must not be empty")
        reserved = {"model", "instructions"}.intersection(self.agent_config)
        if reserved:
            raise ValueError(
                "agent_config duplicates profile fields: "
                f"{', '.join(sorted(reserved))}"
            )
        if self.queue_limit <= 0 or self.backlog_limit < self.queue_limit:
            raise ValueError("invalid specialist queue limits")
        if self.queue_policy not in QUEUE_POLICIES:
            raise ValueError("invalid specialist queue policy")
        if self.session_mode not in {"ephemeral", "resident", "durable"}:
            raise ValueError("invalid specialist session mode")
        if self.idle_timeout_seconds is not None and self.idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if self.completion_retry_limit < 0:
            raise ValueError("completion_retry_limit must not be negative")
        if self.completion_tool is not None and self.completion_retry_limit == 0:
            raise ValueError("completion_retry_limit must be positive with completion_tool")
        return self
