"""Application-owned session creation; each session serves its own agent API."""

from .declarations import endpoint
from .endpoints import HttpError


class SessionsApi:
    def __init__(self, application):
        self.application = application

    @endpoint("get", "/api/v1/sessions", authorization={"scope": "session:read"})
    def list(self):
        return {"sessions": self.application.list_sessions()}

    @endpoint("post", "/api/v1/sessions", request={
        "type": "object", "properties": {
            "session_id": {"type": ["string", "null"]},
            "agent": {"type": ["string", "null"]},
            "durable": {"type": "boolean"},
        }, "additionalProperties": False,
    }, authorization={"scope": "session:write"}, status_code=201)
    async def open(self, session_id=None, agent=None, durable=True):
        try:
            runtime = await self.application.open_session(session_id=session_id, agent=agent, durable=durable)
        except ValueError as error:
            raise HttpError(422, "invalid_session", str(error)) from error
        return {"id": runtime.ready.session_id, "agent": runtime.ready.name,
                "api": f"/sessions/{runtime.ready.session_id}/api/v1"}

    @endpoint("delete", "/api/v1/sessions/{session_id}", request={
        "type": "object", "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"], "additionalProperties": False,
    }, authorization={"scope": "session:write"})
    async def close(self, session_id):
        return {"closed": await self.application.close_session(session_id)}
