"""Current model-to-tokenizer approximation, revisable with backend support."""

from the_framework.utils.tokens import tokenizer_for_model

def test_gpt_5_family_uses_the_local_o200k_tokenizer():
    assert tokenizer_for_model("gpt-5.4").name == "o200k_base"
    assert tokenizer_for_model("gpt-5.6-luna").name == "o200k_base"
    assert tokenizer_for_model("gpt-5.6-sol").name == "o200k_base"
