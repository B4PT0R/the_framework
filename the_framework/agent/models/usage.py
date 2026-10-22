from time import time

from .base import Base


class TokenDetails(Base):
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


class ResponseUsage(Base):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_tokens_details: TokenDetails | None = None
    output_tokens_details: TokenDetails | None = None

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return cls(payload)


class CacheHitStats(Base):
    """Per-response EMA of API-reported cached/input tokens (not local estimates).

    alpha=0.1 gives a 19-sample conventional span, ~6.6-sample half-life.
    Missing/invalid observations leave the EMA unchanged, never count as misses.
    """

    alpha: float = 0.1
    ema_percent: float | None = None
    last_percent: float | None = None
    samples: int = 0
    unreported: int = 0
    updated_at: float | None = None

    def observe(self, usage):
        result = self.copy()
        result.last_percent = None
        tokens = usage.input_tokens if usage is not None else None
        details = usage.input_tokens_details if usage is not None else None
        cached = details.cached_tokens if details is not None else None
        if (type(tokens) is not int or tokens <= 0
                or type(cached) is not int or not 0 <= cached <= tokens):
            result.unreported += 1
            return result
        percent = 100.0 * cached / tokens
        result.last_percent = percent
        result.ema_percent = (
            percent if self.ema_percent is None
            else self.alpha * percent + (1 - self.alpha) * self.ema_percent
        )
        result.samples += 1
        result.updated_at = time()
        return result


class UsageWindow(Base):
    used_percent: float
    limit_window_seconds: int
    reset_at: int | None = None


def usage_window(payload, *, duration_seconds, fallback=None):
    """Select one authenticated-account quota window without guessing across meters."""
    rate_limit = payload.get("rate_limit") if isinstance(payload, dict) else None
    if not isinstance(rate_limit, dict):
        return None
    windows = [
        value
        for value in rate_limit.values()
        if isinstance(value, dict) and value.get("used_percent") is not None
    ]
    exact = next(
        (
            value for value in windows
            if value.get("limit_window_seconds") == duration_seconds
        ),
        None,
    )
    selected = exact
    if selected is None and fallback:
        candidate = rate_limit.get(fallback)
        if (
            isinstance(candidate, dict)
            and candidate.get("used_percent") is not None
            and candidate.get("limit_window_seconds") is None
        ):
            selected = candidate
    if selected is None:
        return None
    return UsageWindow(
        used_percent=float(selected["used_percent"]),
        limit_window_seconds=int(selected.get("limit_window_seconds") or duration_seconds),
        reset_at=(int(selected["reset_at"]) if selected.get("reset_at") is not None else None),
    )
