from .protocol import ApplicationRequest
from core.utils.ids import timestamp_id


class ApplicationError(RuntimeError):
    pass


class Application:
    def __init__(self):
        self.request = None

    def bind(self, request):
        self.request = request

    async def call(self, capability, method, payload=None, *, timeout_ms=30_000):
        if self.request is None:
            raise ApplicationError("application bridge is not available")
        return await self.request(ApplicationRequest(
            id=timestamp_id(),
            capability=capability,
            method=method,
            payload=payload or {},
            timeout_ms=timeout_ms,
        ))
