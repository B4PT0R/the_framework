"""Private specialist declarations and typed task/result payloads."""

from modict import modict

from ..models.responses import ResponseItem
from ..context.session import SessionWatermark
from ..models.base import Base
from ..spec import AgentSpec



def agent_trigger(
    agent,
    *,
    observes=(),
    item_kinds=(),
    conversation_roles=(),
    input_item_field=None,
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
        payload = dict(payload)
        # Atomic wire migration from the former field name.
        if "auxiliary_session_id" in payload and "agent_session_id" not in payload:
            payload["agent_session_id"] = payload.pop("auxiliary_session_id")
        return cls(**{
            **payload,
            "watermark": SessionWatermark(**payload["watermark"]),
        })


class AgentTrigger(Base):
    _config = modict.config(enforce_json=False, auto_convert=False)
    name: str
    spec: AgentSpec
    observes: list[str] = modict.factory(list)
    item_kinds: list[str] = modict.factory(list)
    conversation_roles: list[str] = modict.factory(list)
    input_item_field: str | None = None

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
        return self.spec.fleet_profile(
            identity=self.name, observes=self.observes, item_kinds=self.item_kinds,
            conversation_roles=self.conversation_roles, input_item_field=self.input_item_field,
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
        from core.utils.ids import timestamp_id

        request = AgentTaskRequested(
            request_id=timestamp_id(),
            profile=name,
            prompt=prompt,
            source_item_ids=list(source_item_ids),
        )
        self.agent.emit(request)
        return request.request_id

    def call(self, name, prompt, *, source_item_ids=()):
        return self.submit(name, prompt, source_item_ids=source_item_ids)

    def publish(self, result):
        return self.dispatch(AgentResult.from_dict(result))


__all__ = [
    "AgentResult",
    "AgentTask",
    "AgentTrigger",
    "AgentTriggers",
    "agent_trigger",
]
