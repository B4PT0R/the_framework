"""Named, application-composable projections of canonical session turns."""

from __future__ import annotations

from collections.abc import Callable

from ..models.responses import Artifact, FunctionCall, Message


def _display(items):
    return [item for item in items if isinstance(item, Artifact) or (
        isinstance(item, Message) and item.role in {"user", "assistant"}
    )]


def _remote(items):
    return [item for item in items if isinstance(item, (Artifact, FunctionCall)) or (
        isinstance(item, Message) and item.role in {"user", "assistant"}
    )]


class SessionProjections:
    """Registry of pure turn projections used by worker page requests."""

    def __init__(self, projections=None):
        self._projections = {"full": list, "display": _display, "remote": _remote}
        for name, projection in dict(projections or {}).items():
            self.add(name, projection)

    def add(self, name, projection):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("session projection name is required")
        if name in self._projections:
            raise ValueError(f"duplicate session projection: {name}")
        if not isinstance(projection, Callable):
            raise TypeError("session projection must be callable")
        self._projections[name] = projection
        return projection

    def project(self, name, items):
        try:
            projection = self._projections[name]
        except KeyError as error:
            raise ValueError(f"unsupported session page projection: {name}") from error
        result = projection(list(items))
        if not isinstance(result, (list, tuple)):
            raise TypeError(f"session projection {name} must return a sequence")
        return list(result)

    def names(self):
        return tuple(self._projections)
