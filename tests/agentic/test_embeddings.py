from types import SimpleNamespace

import pytest

from harness_core.plugins.embeddings import embed_texts, normalize_embedding


class Embeddings:
    def __init__(self):
        self.calls = []

    def create(self, *, model, input, dimensions):
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        return SimpleNamespace(data=[
            SimpleNamespace(embedding=[3.0, 4.0, *([0.0] * (dimensions - 2))])
            for _text in input
        ])


def test_normalized_embedding_matches_codex_agent_format():
    vector = normalize_embedding([3.0, 4.0], dimensions=2, precision=5)

    assert vector == [0.6, 0.8]
    with pytest.raises(ValueError, match="positive L2 norm"):
        normalize_embedding([0.0, 0.0], dimensions=2)


def test_embedding_helper_batches_and_normalizes_texts():
    client = SimpleNamespace(embeddings=Embeddings())

    texts = embed_texts(
        client, ["black silk", "moonlit room"],
        dimensions=128,
        precision=5,
    )

    assert len(texts) == 2
    assert all(len(vector) == 128 for vector in texts)
    assert client.embeddings.calls[0]["input"] == ["black silk", "moonlit room"]
    assert all(call["dimensions"] == 128 for call in client.embeddings.calls)
