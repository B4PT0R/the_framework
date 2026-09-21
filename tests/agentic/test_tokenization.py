import pytest

import core.utils.tokens as tokenization
from core.utils.tokens import token_count, token_count_payload, tokenizer_for_model


def test_gpt_5_family_uses_the_local_o200k_tokenizer():
    assert tokenizer_for_model("gpt-5.4").name == "o200k_base"
    assert tokenizer_for_model("gpt-5.6-luna").name == "o200k_base"
    assert tokenizer_for_model("gpt-5.6-sol").name == "o200k_base"


def test_token_count_accepts_arbitrary_user_text_and_structured_payloads():
    text = "Une balise littérale <|endoftext|> reste du contenu utilisateur."

    assert token_count(text, model="gpt-5.4") > 0
    assert token_count_payload(
        {"input": [{"type": "input_text", "text": text}]},
        model="gpt-5.4",
    ) > token_count(text, model="gpt-5.4")


def test_truncate_middle_preserves_both_ends_and_reports_omitted_tokens(monkeypatch):
    monkeypatch.setattr(
        tokenization,
        "token_count",
        lambda text, model=None: len(text),
    )

    result = tokenization.truncate_middle("aa\nbb\ncc\n", max_tokens=4)

    assert result.startswith("aa")
    assert result.endswith("cc\n")
    assert "[Middle truncated: original output had 9 tokens; kept first 2 and last 2 tokens]" in result


def test_truncate_middle_handles_one_oversized_line(monkeypatch):
    monkeypatch.setattr(
        tokenization,
        "token_count",
        lambda text, model=None: len(text),
    )

    result = tokenization.truncate_middle("abcdefghij", max_tokens=4)

    assert result.startswith("ab")
    assert result.endswith("ij")
    assert "original output had 10 tokens" in result
    with pytest.raises(ValueError, match="positive integer"):
        tokenization.truncate_middle("content", max_tokens=0)
