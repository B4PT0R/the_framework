"""Validated normalized embeddings shared by semantic plugins."""

import math


def normalize_embedding(vector, *, dimensions=128, precision=5):
    """Match codex-agent's L2-normalized, fixed-precision embedding format."""
    values = [float(value) for value in vector]
    if len(values) != dimensions:
        raise ValueError(
            f"embedding has {len(values)} dimensions; expected {dimensions}"
        )
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("embedding vector must have a finite positive L2 norm")
    inverse = 1.0 / norm
    return [round(value * inverse, precision) for value in values]


def embed_texts(
    client,
    texts,
    *,
    model="text-embedding-3-small",
    dimensions=128,
    precision=5,
):
    texts = list(texts)
    if not texts:
        return []
    if not all(isinstance(text, str) and text.strip() for text in texts):
        raise ValueError("embedding texts must be non-empty strings")
    response = client.embeddings.create(
        model=model,
        input=texts,
        dimensions=dimensions,
    )
    if len(response.data) != len(texts):
        raise RuntimeError(
            f"embedding backend returned {len(response.data)} vectors for "
            f"{len(texts)} texts"
        )
    return [
        normalize_embedding(
            item.embedding,
            dimensions=dimensions,
            precision=precision,
        )
        for item in response.data
    ]
