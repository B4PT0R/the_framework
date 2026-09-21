from collections.abc import Mapping

from .base import Base


class State(Base):
    """Typed mutable plugin state, kept separate from user configuration."""


class States(dict):
    """Typed state registry owned by one canonical agent process."""

    def __init__(self, values=None):
        super().__init__()
        self._initial = dict(values or {})

    def add(self, name, state_cls=State):
        if not isinstance(state_cls, type) or not issubclass(state_cls, State):
            raise ValueError("plugin state must be a State subclass")
        payload = self._initial.pop(name, {})
        if not isinstance(payload, Mapping):
            raise ValueError(f"plugin state must be a mapping: {name}")
        state = state_cls(**payload)
        self[name] = state
        return state

    def snapshot(self):
        return {name: dict(value) for name, value in self.items()}
