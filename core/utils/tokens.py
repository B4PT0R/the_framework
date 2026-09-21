import json
from functools import lru_cache

import tiktoken

DEFAULT_TOKEN_MODEL = "gpt-5.6-luna"
DEFAULT_MAX_TOOL_OUTPUT_TOKENS = 8_000
MODEL_ENCODING_ALIASES = {
    "gpt-5.6-sol": "o200k_base",
    "gpt-5.6-terra": "o200k_base",
    "gpt-5.6-luna": "o200k_base",
    "gpt-5.5": "o200k_base",
    "gpt-5.4": "o200k_base",
    "gpt-5.4-mini": "o200k_base",
    "gpt-5.3-codex": "o200k_base",
    "gpt-5.3-codex-spark": "o200k_base",
    "gpt-5.2": "o200k_base",
}


@lru_cache(maxsize=None)
def tokenizer_for_model(model=None):
    model = str(model or DEFAULT_TOKEN_MODEL)
    encoding = MODEL_ENCODING_ALIASES.get(model)
    if encoding is not None:
        return tiktoken.get_encoding(encoding)
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(
            "o200k_base" if model.startswith("gpt-5") else "cl100k_base"
        )


def token_count(text, *, model=None):
    return len(
        tokenizer_for_model(model).encode(
            str(text or ""),
            disallowed_special=(),
        )
    )


def token_count_payload(payload, *, model=None):
    if payload is None:
        return 0
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return token_count(serialized, model=model)


def _fit_edge(text, *, max_tokens, model=None, tail=False):
    if max_tokens <= 0 or not text:
        return ""
    low = 0
    high = len(text)
    while low < high:
        length = (low + high + 1) // 2
        candidate = text[-length:] if tail else text[:length]
        if token_count(candidate, model=model) <= max_tokens:
            low = length
        else:
            high = length - 1
    return text[-low:] if tail and low else text[:low]


def truncate_middle(text, *, max_tokens, model=None):
    """Keep token-bounded text from both ends and mark the omitted middle."""
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")

    text = str(text or "")
    total_tokens = token_count(text, model=model)
    if total_tokens <= max_tokens:
        return text

    trailing_newline = "\n" if text.endswith("\n") else ""
    body = text[:-1] if trailing_newline else text
    head_budget = (max_tokens + 1) // 2
    tail_budget = max_tokens - head_budget
    head = _fit_edge(body, max_tokens=head_budget, model=model)
    remaining = body[len(head):]
    tail = _fit_edge(remaining, max_tokens=tail_budget, model=model, tail=True)
    head_tokens = token_count(head, model=model)
    tail_tokens = token_count(tail, model=model)
    marker = (
        "\n\n...\n\n"
        f"[Middle truncated: original output had {total_tokens} tokens; "
        f"kept first {head_tokens} and last {tail_tokens} tokens]"
        "\n\n...\n\n"
    )
    return f"{head}{marker}{tail}{trailing_newline}"
