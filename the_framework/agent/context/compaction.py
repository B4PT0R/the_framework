from ..models.usage import ResponseUsage
from ..models.responses import ResponseItem
from ..models.base import Base


class CompactionResponse(Base):
    id: str
    object: str = "response.compaction"
    created_at: int | None = None
    output: list[ResponseItem]
    usage: ResponseUsage | None = None

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return cls(
            **{
                **payload,
                "output": [
                    ResponseItem.from_dict(item)
                    for item in payload.get("output", [])
                ],
                "usage": (
                    ResponseUsage.from_dict(payload["usage"])
                    if payload.get("usage") is not None
                    else None
                ),
            }
        )
