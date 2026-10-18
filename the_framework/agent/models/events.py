"""Framework event root. Backend wire events never enter this registry."""
from modict import modict
from .base import TypedBase, type_cls


class Event(TypedBase):
    _config = modict.config(extra="forbid")

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            if cls is Event and type_cls(Event, payload.type) is Event:
                raise ValueError(f"unknown Framework event: {payload.type}")
            return payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        if cls is Event and type_cls(cls, payload.get("type", "")) is cls:
            raise ValueError(f"unknown Framework event: {payload.get('type')}")
        return super().from_dict(payload)
