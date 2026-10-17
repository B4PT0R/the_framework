"""One local browser session; no credentials in URLs or browser storage."""

from hmac import compare_digest

from the_framework.server.api.endpoints import Principal
from the_framework.server.clients.previews import (
    authorize_preview,
    is_preview,
    preview_access,
)


class LocalSecurity:
    cookie = "starter_session"

    def __init__(self, token, *, origin, browser_token=None):
        if not token:
            raise ValueError("a private local session token is required")
        self.token = token
        self.browser_token = token if browser_token is None else browser_token
        if not self.browser_token:
            raise ValueError("a private browser session token is required")
        self.origin = origin.rstrip("/")

    async def authenticate(self, request):
        # Exact host/origin checks also reject DNS rebinding and cross-site
        # cookie-bearing requests. Playwright installs the HttpOnly cookie.
        from urllib.parse import urlsplit

        if request.headers.get("host") != urlsplit(self.origin).netloc:
            return None
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            return None
        previews = preview_access(request)
        if previews is not None and previews.cookie in request.cookies:
            principal = previews.authenticate(request)
            if request.scope['type'] == 'http' and not previews.allows_http(request):
                return None
            return principal
        bearer = request.headers.get("authorization", "")
        supplied = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(self.cookie, "")
        expected = self.token if bearer.startswith("Bearer ") else self.browser_token
        if not isinstance(supplied, str) or not compare_digest(supplied.encode(), expected.encode()):
            return None
        return Principal(id="local", scopes=frozenset({"*"}))

    async def authorize(self, principal, requirement, request):
        if is_preview(principal):
            return authorize_preview(principal, requirement)
        return principal.id == "local"

    async def handshake(self, socket):
        if socket.headers.get("origin") != self.origin:
            return None
        principal = await self.authenticate(socket)
        if principal is None:
            return None
        role = 'preview' if is_preview(principal) else 'primary'
        if role == 'preview' and socket.url.path != '/api/v1/events':
            return None
        return {"subprotocol": None, **({"client_role": "preview"} if role == "preview" else {})}
