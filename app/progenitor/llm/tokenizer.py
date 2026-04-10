"""Tokenizer utilities using tiktoken for accurate token counting.

This module provides accurate token counting for OpenAI models using tiktoken,
with fallback to estimation when tiktoken is unavailable.

FEAT: Token counting accuracy improvement using tiktoken.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import tiktoken

logger = logging.getLogger(__name__)

# Track if tiktoken is available
_TIKTOKEN_AVAILABLE = False
_tiktoken = None

try:
    import tiktoken as _tiktoken  # type: ignore

    _TIKTOKEN_AVAILABLE = True
    logger.info("tiktoken available - using accurate token counting")
except ImportError:
    logger.warning(
        "tiktoken not installed - using estimated token counts. Install with: pip install tiktoken"
    )


# Model to encoding mapping
MODEL_ENCODINGS = {
    # GPT-4 models
    "gpt-4": "cl100k_base",
    "gpt-4-32k": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",
    "gpt-4-turbo-preview": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    # GPT-3.5 models
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5-turbo-16k": "cl100k_base",
    # Text completion models
    "text-davinci-003": "p50k_base",
    "text-davinci-002": "p50k_base",
    # Embedding models
    "text-embedding-ada-002": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
}

# Default encoding for unknown models
DEFAULT_ENCODING = "cl100k_base"


@lru_cache(maxsize=8)
def get_encoding(model: str | None = None) -> tiktoken.Encoding | None:
    """Get tiktoken encoding for a model.

    Args:
        model: Model name (e.g., 'gpt-4', 'gpt-3.5-turbo').
            If None, uses default encoding.

    Returns:
        tiktoken Encoding object, or None if tiktoken unavailable.
    """
    if not _TIKTOKEN_AVAILABLE or _tiktoken is None:
        return None

    try:
        # Try to get encoding by model name first
        if model:
            # Check our mapping
            encoding_name = MODEL_ENCODINGS.get(model)
            if encoding_name:
                return _tiktoken.get_encoding(encoding_name)

            # Try tiktoken's built-in model mapping
            try:
                return _tiktoken.encoding_for_model(model)
            except KeyError:
                logger.debug(f"Unknown model '{model}', using default encoding")

        # Fall back to default
        return _tiktoken.get_encoding(DEFAULT_ENCODING)

    except Exception as e:
        logger.warning(f"Failed to get encoding: {e}")
        return None


def count_tokens(text: str, model: str | None = None) -> int:
    """Count tokens in a text string.

    Uses tiktoken for accurate counting when available,
    falls back to character-based estimation otherwise.

    Args:
        text: Text to count tokens for.
        model: Model name for encoding selection.

    Returns:
        Token count (exact if tiktoken available, estimated otherwise).
    """
    if not text:
        return 0

    encoding = get_encoding(model)
    if encoding:
        return len(encoding.encode(text))

    # Fallback: estimate ~4 characters per token
    return max(1, len(text) // 4)


def count_message_tokens(
    messages: Sequence[dict],
    model: str | None = None,
) -> int:
    """Count tokens for a list of chat messages.

    Accounts for message formatting overhead (role, name tokens, etc.)
    as used by the OpenAI Chat API.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.
        model: Model name for encoding selection.

    Returns:
        Estimated token count for the full message sequence.
    """
    if not messages:
        return 0

    encoding = get_encoding(model)
    if not encoding:
        # Fallback estimation
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            msg.get("role", "")
            # Estimate: content + role + formatting overhead
            total += max(1, len(content) // 4) + 4
        return total

    # Token counting with message formatting overhead
    # Based on OpenAI's guidelines for chat models
    tokens_per_message = 3  # <|start|>{role/name}\n{content}<|end|>\n
    tokens_per_name = 1  # If name is present

    total = 0
    for msg in messages:
        total += tokens_per_message
        for key, value in msg.items():
            if value:
                total += len(encoding.encode(str(value)))
            if key == "name":
                total += tokens_per_name

    # Every reply is primed with <|start|>assistant<|message|>
    total += 3

    return total


def estimate_tokens_naive(text: str) -> int:
    """Naive token estimation (4 chars per token).

    Use this when performance is critical and accuracy isn't.

    Args:
        text: Text to estimate.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    model: str | None = None,
    suffix: str = "...",
) -> str:
    """Truncate text to fit within a token limit.

    Args:
        text: Text to truncate.
        max_tokens: Maximum allowed tokens.
        model: Model name for encoding selection.
        suffix: Suffix to append if truncated.

    Returns:
        Original text if within limit, truncated text otherwise.
    """
    if not text or max_tokens <= 0:
        return ""

    encoding = get_encoding(model)
    if not encoding:
        # Fallback: estimate chars needed
        max_chars = max_tokens * 4 - len(suffix)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + suffix

    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text

    # Reserve space for suffix
    suffix_tokens = len(encoding.encode(suffix)) if suffix else 0
    keep_tokens = max_tokens - suffix_tokens

    if keep_tokens <= 0:
        return suffix

    truncated_tokens = tokens[:keep_tokens]
    return str(encoding.decode(truncated_tokens)) + suffix


def is_tiktoken_available() -> bool:
    """Check if tiktoken is available.

    Returns:
        True if tiktoken is installed and functional.
    """
    return _TIKTOKEN_AVAILABLE


def get_token_stats(text: str, model: str | None = None) -> dict:
    """Get detailed token statistics for text.

    Args:
        text: Text to analyze.
        model: Model name for encoding selection.

    Returns:
        Dictionary with token statistics.
    """
    if not text:
        empty_count = 0
        empty_ratio = 0.0
        return {
            "token_count": empty_count,
            "char_count": empty_count,
            "chars_per_token": empty_ratio,
            "using_tiktoken": _TIKTOKEN_AVAILABLE,
            "model": model,
        }

    token_count = count_tokens(text, model)
    char_count = len(text)

    return {
        "token_count": token_count,
        "char_count": char_count,
        "chars_per_token": char_count / token_count if token_count else 0.0,
        "using_tiktoken": _TIKTOKEN_AVAILABLE,
        "model": model,
    }


__all__ = [
    "count_tokens",
    "count_message_tokens",
    "estimate_tokens_naive",
    "truncate_to_tokens",
    "get_encoding",
    "is_tiktoken_available",
    "get_token_stats",
    "MODEL_ENCODINGS",
    "DEFAULT_ENCODING",
]
