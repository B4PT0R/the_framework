"""Private specialist declarations and typed task/result payloads."""

from typing import Literal

from modict import modict

from ..models.responses import ResponseItem
from ..context.session import SessionWatermark
from ..models.base import Base
from ..spec import AgentSpec
from ..models.worker import WorkerProfile
from .instructions import Instruction



class QueuePolicy(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", enforce_json=True)
    mode: Literal["fifo", "latest"] = "fifo"
    limit: int = 8
    durable: bool = True
    backlog_limit: int = 512

    @modict.model_validator(mode="after")
    def validate_limits(self):
        if self.limit <= 0 or self.backlog_limit < self.limit:
            raise ValueError("invalid agent queue limits")

    @classmethod
    def fifo(cls, *, limit=8, durable=True, backlog_limit=512):
        return cls(mode="fifo", limit=limit, durable=durable, backlog_limit=backlog_limit)

    @classmethod
    def latest(cls, *, limit=1, durable=False, backlog_limit=None):
        return cls(mode="latest", limit=limit, durable=durable,
                   backlog_limit=limit if backlog_limit is None else backlog_limit)


def agent_trigger(
    agent,
    *,
    observes=(),
    item_kinds=(),
    conversation_roles=(),
    input_item_field=None,
    durable=False,
    retain_history=False,
    activity_visible=True,
    queue=None,
    idle_timeout_seconds=None,
    completion_tool=None,
    completion_retry_limit=0,
):
    """Declare a private specialist trigger owned by the surrounding plugin."""
    if not isinstance(agent, AgentSpec):
        raise TypeError("agent_trigger requires an AgentSpec")
    def decorator(func):
        func.agent_trigger = {
            "spec": agent,
            "observes": list(observes),
            "item_kinds": list(item_kinds),
            "conversation_roles": list(conversation_roles),
            "input_item_field": input_item_field,
            "durable": durable,
            "retain_history": retain_history,
            "activity_visible": activity_visible,
            "queue": QueuePolicy() if queue is None else queue,
            "idle_timeout_seconds": idle_timeout_seconds,
            "completion_tool": completion_tool,
            "completion_retry_limit": completion_retry_limit,
        }
        return func
    return decorator


class AgentTask(Base):
    id: str
    profile: str
    prompt: str
    watermark: SessionWatermark
    observation_type: str
    metadata: dict = modict.factory(dict)
    input_items: list[ResponseItem] = modict.factory(list)

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        return cls(**{
            **payload,
            "watermark": SessionWatermark(**payload["watermark"]),
            "input_items": [
                ResponseItem.from_dict(item) for item in payload.get("input_items", [])
            ],
        })


class AgentResult(Base):
    id: str
    task_id: str
    profile: str
    status: str
    watermark: SessionWatermark
    output: str | None = None
    error: str | None = None
    agent_session_id: str | None = None
    metadata: dict = modict.factory(dict)

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        return cls(**{
            **payload,
            "watermark": SessionWatermark(**payload["watermark"]),
        })


class AgentTrigger(Base):
    _config = modict.config(enforce_json=False, auto_convert=False)
    name: str = ""
    spec: AgentSpec
    durable: bool = False
    retain_history: bool = False
    activity_visible: bool = True
    queue: QueuePolicy = modict.factory(QueuePolicy)
    idle_timeout_seconds: float | int | None = None
    completion_tool: str | None = None
    completion_retry_limit: int = 0
    observes: list[str] = modict.factory(list)
    item_kinds: list[str] = modict.factory(list)
    conversation_roles: list[str] = modict.factory(list)
    input_item_field: str | None = None

    @modict.model_validator(mode="after")
    def validate_execution(self):
        if not self.name:
            self.name = self.spec.name
        if self.idle_timeout_seconds is not None and self.idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        if self.completion_retry_limit < 0:
            raise ValueError("completion_retry_limit must not be negative")
        if self.completion_retry_limit and not self.completion_tool:
            raise ValueError("completion retries require a completion tool")

    @classmethod
    def from_function(cls, func):
        declaration = getattr(func, "agent_trigger", None)
        if declaration is None:
            raise ValueError("function is not decorated with @agent_trigger")
        spec = declaration["spec"]
        if spec.description == "Agent worker." and func.__doc__:
            spec = AgentSpec({**spec, "description": func.__doc__.strip()})
        return cls(
            **{**declaration, "name": spec.name, "spec": spec},
        ).with_handler(func)

    def with_handler(self, handler):
        self.set_attr("handler", handler)
        return self

    def handle(self, result):
        if not self.has_attr("handler"):
            raise ValueError(f"agent trigger has no result handler: {self.name}")
        return self.handler(result)

    def profile(self):
        """Compile the recipe and this invocation's policy for the worker boundary."""
        spec = self.spec
        instructions = "\n\n".join(
            item.content if isinstance(item, Instruction) else item["content"]
            for item in spec.instructions
        ).strip()
        configuration = dict(spec.configuration)
        model = configuration.pop("model", "gpt-5.6-luna")
        return WorkerProfile(
            name=self.name, description=spec.description,
            instructions=instructions or spec.description, model=model,
            agent_config=configuration,
            plugins=[plugin for plugin in spec.extensions if isinstance(plugin, str)],
            durable=self.durable, retain_history=self.retain_history,
            activity_visible=self.activity_visible,
            queue_limit=self.queue.limit, queue_policy=self.queue.mode,
            durable_tasks=self.queue.durable, backlog_limit=self.queue.backlog_limit,
            idle_timeout_seconds=self.idle_timeout_seconds,
            completion_tool=self.completion_tool, completion_retry_limit=self.completion_retry_limit,
            observes=list(self.observes), item_kinds=list(self.item_kinds),
            conversation_roles=list(self.conversation_roles), input_item_field=self.input_item_field,
        )


class AgentTriggers(Base[str, AgentTrigger]):
    _config = modict.config(enforce_json=False, auto_convert=False)

    def __init__(self, *args, agent=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_attr("agent", agent)

    def clone_empty(self):
        return type(self)(agent=self.agent)

    def add(self, trigger_or_func):
        value = (
            trigger_or_func
            if isinstance(trigger_or_func, AgentTrigger)
            else AgentTrigger.from_function(trigger_or_func)
        )
        if value.name in self:
            raise ValueError(f"duplicate agent trigger: {value.name}")
        self[value.name] = value
        return value

    def profiles(self):
        return [value.profile() for value in self.values()]

    def dispatch(self, result):
        profile = result.get("profile") if isinstance(result, dict) else result.profile
        if profile not in self:
            raise ValueError(f"unknown agent result profile: {profile}")
        return self[profile].handle(result)

    def submit(self, name, prompt, *, source_item_ids=()):
        """Request bounded work from an agent owned by the current plugin."""
        if self.agent is None:
            raise RuntimeError("agent trigger registry has no canonical owner")
        if name not in self:
            raise ValueError(f"unknown private agent: {name}")
        from ..models.lifecycle import AgentTaskRequested
        from the_framework.utils.ids import timestamp_id

        request = AgentTaskRequested(
            request_id=timestamp_id(),
            profile=name,
            prompt=prompt,
            source_item_ids=list(source_item_ids),
        )
        self.agent.emit(request)
        return request.request_id



__all__ = [
    "AgentResult",
    "AgentTask",
    "AgentTrigger",
    "AgentTriggers",
    "agent_trigger",
]
