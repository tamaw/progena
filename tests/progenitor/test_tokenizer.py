"""Tests for LLM tokenizer module.

Tests accurate token counting with tiktoken and fallback behavior.
"""

from unittest.mock import patch

from app.progenitor.llm.tokenizer import (
    DEFAULT_ENCODING,
    MODEL_ENCODINGS,
    count_message_tokens,
    count_tokens,
    estimate_tokens_naive,
    get_encoding,
    get_token_stats,
    is_tiktoken_available,
    truncate_to_tokens,
)


class TestCountTokens:
    """Tests for count_tokens function."""

    def test_empty_string_returns_zero(self):
        """Empty string should return 0 tokens."""
        assert count_tokens("") == 0

    def test_none_equivalent_returns_zero(self):
        """None/empty should return 0."""
        assert count_tokens("") == 0

    def test_short_text(self):
        """Short text should return reasonable token count."""
        result = count_tokens("Hello, world!")
        assert result > 0
        assert result < 10  # Should be 3-4 tokens

    def test_long_text(self):
        """Longer text should have proportionally more tokens."""
        short = count_tokens("Hi")
        long = count_tokens("Hello, this is a much longer piece of text.")
        assert long > short

    def test_with_model_parameter(self):
        """Should accept model parameter."""
        result = count_tokens("Hello", model="gpt-4")
        assert result > 0

    def test_different_models(self):
        """Different models should work (may have same/different counts)."""
        text = "Hello, world!"
        gpt4_count = count_tokens(text, model="gpt-4")
        gpt35_count = count_tokens(text, model="gpt-3.5-turbo")
        # Both should return valid counts
        assert gpt4_count > 0
        assert gpt35_count > 0

    def test_unknown_model_uses_default(self):
        """Unknown model should fall back to default encoding."""
        result = count_tokens("Hello", model="unknown-model-xyz")
        assert result > 0

    def test_unicode_text(self):
        """Unicode text should be handled correctly."""
        result = count_tokens("Hello 👋 世界!")
        assert result > 0

    def test_whitespace_handling(self):
        """Whitespace should be counted."""
        with_spaces = count_tokens("a b c d e")
        without_spaces = count_tokens("abcde")
        # They may differ due to tokenization
        assert with_spaces >= without_spaces

    def test_newlines(self):
        """Newlines should be counted."""
        text = "Line 1\nLine 2\nLine 3"
        result = count_tokens(text)
        assert result > 0


class TestCountMessageTokens:
    """Tests for count_message_tokens function."""

    def test_empty_list_returns_zero(self):
        """Empty message list should return 0."""
        assert count_message_tokens([]) == 0

    def test_single_message(self):
        """Single message should have tokens + overhead."""
        messages = [{"role": "user", "content": "Hello"}]
        result = count_message_tokens(messages)
        assert result > 0

    def test_multiple_messages(self):
        """Multiple messages should sum tokens."""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = count_message_tokens(messages)
        # Should be more than single message
        single = count_message_tokens([messages[0]])
        assert result > single

    def test_message_with_name(self):
        """Message with name field should have extra token overhead."""
        without_name = [{"role": "user", "content": "Hello"}]
        with_name = [{"role": "user", "content": "Hello", "name": "Alice"}]

        count_without = count_message_tokens(without_name)
        count_with = count_message_tokens(with_name)

        # Name adds tokens (the name itself + 1 overhead)
        assert count_with > count_without

    def test_empty_content(self):
        """Message with empty content should still have overhead."""
        messages = [{"role": "user", "content": ""}]
        result = count_message_tokens(messages)
        # Should have at least role and formatting overhead
        assert result >= 3

    def test_with_model_parameter(self):
        """Should accept model parameter."""
        messages = [{"role": "user", "content": "Hello"}]
        result = count_message_tokens(messages, model="gpt-4")
        assert result > 0


class TestEstimateTokensNaive:
    """Tests for naive estimation fallback."""

    def test_empty_returns_zero(self):
        """Empty string returns 0."""
        assert estimate_tokens_naive("") == 0

    def test_four_chars_per_token(self):
        """Should estimate ~4 chars per token."""
        # 20 chars should be ~5 tokens
        result = estimate_tokens_naive("12345678901234567890")
        assert result == 5

    def test_minimum_one_token(self):
        """Should return at least 1 for non-empty."""
        assert estimate_tokens_naive("Hi") >= 1
        assert estimate_tokens_naive("A") >= 1


class TestTruncateToTokens:
    """Tests for truncate_to_tokens function."""

    def test_empty_text(self):
        """Empty text returns empty."""
        assert truncate_to_tokens("", 100) == ""

    def test_zero_max_tokens(self):
        """Zero max tokens returns empty."""
        assert truncate_to_tokens("Hello world", 0) == ""

    def test_text_within_limit(self):
        """Text within limit is returned unchanged."""
        text = "Hello"
        result = truncate_to_tokens(text, 100)
        assert result == text

    def test_text_exceeds_limit(self):
        """Text exceeding limit is truncated."""
        text = "This is a longer text that will need to be truncated to fit."
        result = truncate_to_tokens(text, 5)
        assert len(result) < len(text)
        assert result.endswith("...")

    def test_custom_suffix(self):
        """Custom suffix should be used."""
        text = "This is a longer text that will need to be truncated to fit."
        result = truncate_to_tokens(text, 5, suffix="[...]")
        assert result.endswith("[...]")

    def test_no_suffix(self):
        """No suffix should work."""
        text = "This is a longer text that will need to be truncated to fit."
        result = truncate_to_tokens(text, 5, suffix="")
        assert not result.endswith("...")

    def test_preserves_meaning(self):
        """Truncated text should preserve beginning."""
        text = "Hello world this is a test"
        result = truncate_to_tokens(text, 3)
        assert result.startswith("Hello")


class TestGetEncoding:
    """Tests for get_encoding function."""

    def test_default_encoding(self):
        """Should return encoding for default."""
        encoding = get_encoding()
        if is_tiktoken_available():
            assert encoding is not None
        else:
            assert encoding is None

    def test_known_model(self):
        """Should return encoding for known model."""
        encoding = get_encoding("gpt-4")
        if is_tiktoken_available():
            assert encoding is not None

    def test_caching(self):
        """Should cache encodings."""
        enc1 = get_encoding("gpt-4")
        enc2 = get_encoding("gpt-4")
        # Should be the same object (cached)
        assert enc1 is enc2


class TestIsTiktokenAvailable:
    """Tests for is_tiktoken_available function."""

    def test_returns_boolean(self):
        """Should return a boolean."""
        result = is_tiktoken_available()
        assert isinstance(result, bool)


class TestGetTokenStats:
    """Tests for get_token_stats function."""

    def test_empty_text(self):
        """Empty text should return zeros."""
        stats = get_token_stats("")
        assert stats["token_count"] == 0
        assert stats["char_count"] == 0
        assert stats["chars_per_token"] == 0.0

    def test_returns_all_fields(self):
        """Should return all expected fields."""
        stats = get_token_stats("Hello world")
        assert "token_count" in stats
        assert "char_count" in stats
        assert "chars_per_token" in stats
        assert "using_tiktoken" in stats
        assert "model" in stats

    def test_char_count_accurate(self):
        """Character count should be accurate."""
        text = "Hello"
        stats = get_token_stats(text)
        assert stats["char_count"] == 5

    def test_with_model(self):
        """Should include model in stats."""
        stats = get_token_stats("Hello", model="gpt-4")
        assert stats["model"] == "gpt-4"


class TestModelEncodings:
    """Tests for MODEL_ENCODINGS constant."""

    def test_gpt4_encoding(self):
        """GPT-4 should use cl100k_base."""
        assert MODEL_ENCODINGS["gpt-4"] == "cl100k_base"

    def test_gpt4o_encoding(self):
        """GPT-4o should use o200k_base."""
        assert MODEL_ENCODINGS["gpt-4o"] == "o200k_base"

    def test_default_encoding_defined(self):
        """Default encoding should be defined."""
        assert DEFAULT_ENCODING == "cl100k_base"


class TestFallbackBehavior:
    """Tests for behavior when tiktoken is unavailable."""

    def test_count_tokens_fallback(self):
        """count_tokens should use estimation without tiktoken."""
        with patch("app.progenitor.llm.tokenizer.get_encoding", return_value=None):
            # Should fall back to 4 chars per token
            result = count_tokens("12345678")  # 8 chars = 2 tokens
            assert result == 2

    def test_count_message_tokens_fallback(self):
        """count_message_tokens should use estimation without tiktoken."""
        with patch("app.progenitor.llm.tokenizer.get_encoding", return_value=None):
            messages = [{"role": "user", "content": "12345678"}]
            result = count_message_tokens(messages)
            # Should estimate content + overhead
            assert result > 0

    def test_truncate_fallback(self):
        """truncate_to_tokens should use char estimation without tiktoken."""
        with patch("app.progenitor.llm.tokenizer.get_encoding", return_value=None):
            text = "A" * 100  # 100 chars = ~25 tokens
            result = truncate_to_tokens(text, 5)  # 5 tokens = ~20 chars
            # Should truncate
            assert len(result) < len(text)


class TestEdgeCases:
    """Edge case tests."""

    def test_very_long_text(self):
        """Should handle very long text."""
        text = "word " * 10000
        result = count_tokens(text)
        assert result > 0

    def test_special_characters(self):
        """Should handle special characters."""
        text = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        result = count_tokens(text)
        assert result > 0

    def test_code_snippets(self):
        """Should handle code snippets."""
        code = """
def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
"""
        result = count_tokens(code)
        assert result > 0

    def test_mixed_languages(self):
        """Should handle mixed language text."""
        text = "Hello こんにちは Bonjour مرحبا"
        result = count_tokens(text)
        assert result > 0
