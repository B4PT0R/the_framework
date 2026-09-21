"""Declarative, authenticated WebSocket endpoints."""

from __future__ import annotations

import inspect

from fastapi import WebSocket
from modict import modict


class WebSocketEndpoint(modict):
    _config = modict.config(frozen=True, strict=True, extra="forbid", auto_convert=False)

    path: str
    handler: object
    name: str | None = None
    authenticated: bool = True
    authorization: object | None = None
    handshake: object | None = None

    @modict.model_validator(mode="after")
    def validate_declaration(self):
        if not self.path.startswith("/"):
            raise ValueError("WebSocket endpoint path must start with /")
        if not callable(self.handler):
            raise TypeError("WebSocket endpoint handler must be callable")
        if self.handshake is not None and not callable(self.handshake):
            raise TypeError("WebSocket handshake must be callable")
        if self.handshake is not None and self.authorization is not None:
            raise ValueError(
                "custom WebSocket handshakes own authorization and cannot use "
                "a separate authorization requirement"
            )


def websocket(
    path, *, authenticated=True, authorization=None, handshake=None, name=None,
):
    """Declare a WebSocket handler without importing FastAPI in domain code."""
    def decorate(handler):
        return WebSocketEndpoint(
            path=path,
            handler=handler,
            name=name or handler.__name__,
            authenticated=authenticated,
            authorization=authorization,
            handshake=handshake,
        )

    return decorate


class WebSocketRegistry:
    def __init__(self, app, *, security=None):
        self.app = app
        self.security = security
        self.entries = []
        self.paths = {
            route.path
            for route in app.routes
            if route.__class__.__name__ in {"APIWebSocketRoute", "WebSocketRoute"}
        }

    def add(self, endpoint, *, owner="application"):
        if not isinstance(endpoint, WebSocketEndpoint):
            declaration = getattr(endpoint, "websocket_endpoint", None)
            if declaration is None:
                raise TypeError("WebSocket declarations must use @websocket")
            endpoint = declaration
        if endpoint.path in self.paths:
            raise ValueError(f"duplicate WebSocket endpoint: {endpoint.path}")
        if endpoint.handshake is None and endpoint.authenticated and self.security is None:
            raise ValueError(
                "authenticated WebSocket endpoint requires a security policy: "
                f"{endpoint.path}"
            )
        self.paths.add(endpoint.path)
        self.entries.append((endpoint, owner))
        return endpoint

    async def _allowed(self, endpoint, socket):
        if not endpoint.authenticated:
            return True
        principal = self.security.authenticate(socket)
        if inspect.isawaitable(principal):
            principal = await principal
        if principal is None:
            return False
        allowed = self.security.authorize(
            principal, endpoint.authorization, socket
        )
        return await allowed if inspect.isawaitable(allowed) else allowed

    def install(self):
        for endpoint, owner in self.entries:
            def make_dispatch(registered):
                async def dispatch(socket: WebSocket):
                    connection = None
                    if registered.handshake is not None:
                        connection = registered.handshake(socket)
                        if inspect.isawaitable(connection):
                            connection = await connection
                        if connection is None:
                            await socket.close(code=1008)
                            return
                    elif not await self._allowed(registered, socket):
                        await socket.close(code=1008)
                        return
                    result = (
                        registered.handler(socket, connection)
                        if registered.handshake is not None
                        else registered.handler(socket)
                    )
                    if inspect.isawaitable(result):
                        await result

                return dispatch

            self.app.add_api_websocket_route(
                endpoint.path,
                make_dispatch(endpoint),
                name=f"extension:{owner}:{endpoint.name or endpoint.path}",
            )
        return self


__all__ = ["WebSocketEndpoint", "websocket"]
