from uuid import NAMESPACE_URL, uuid5


def resolve_prompt_cache_key(session_id, configured=None):
    """Return an explicit key or a stable backend-compatible session key."""
    if configured is not None:
        return configured
    return str(uuid5(NAMESPACE_URL, f"micro-agent:session:{session_id}"))
