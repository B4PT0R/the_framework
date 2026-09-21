"""One local browser session; no credentials in URLs or browser storage."""

from hmac import compare_digest

from core.server.api.endpoints import Principal


class LocalSecurity:
    cookie = "starter_session"

    def __init__(self, token, *, origin):
        if not token:
            raise ValueError("a private local session token is required")
        self.token = token
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
        bearer = request.headers.get("authorization", "")
        supplied = bearer[7:] if bearer.startswith("Bearer ") else request.cookies.get(self.cookie, "")
        if not isinstance(supplied, str) or not compare_digest(supplied.encode(), self.token.encode()):
            return None
        return Principal(id="local", scopes=frozenset({"*"}))

    async def authorize(self, principal, requirement, request):
        return principal.id == "local"

    async def handshake(self, socket):
        if socket.headers.get("origin") != self.origin:
            return None
        if await self.authenticate(socket) is None:
            return None
        return {"subprotocol": None}
