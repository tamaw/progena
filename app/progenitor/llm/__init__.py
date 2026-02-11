"""LLM module - OpenAI Responses API wrapper and utilities."""

from .circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from .client import LLMClient
from .conversation_compressor import ConversationCompressor, IncrementalCompressor
from .mock_client import MockLLMClient, create_llm_client, is_mock_mode_enabled
from .prompts import PromptTemplates
from .tokenizer import (
    count_message_tokens,
    count_tokens,
    get_token_stats,
    is_tiktoken_available,
    truncate_to_tokens,
)

__all__ = [
    "LLMClient",
    "PromptTemplates",
    # Tokenizer
    "count_tokens",
    "count_message_tokens",
    "truncate_to_tokens",
    "is_tiktoken_available",
    "get_token_stats",
    # Conversation compression
    "ConversationCompressor",
    "IncrementalCompressor",
    # Circuit breaker
    "CircuitBreaker",
    "CircuitState",
    "CircuitOpenError",
    # Mock client
    "MockLLMClient",
    "create_llm_client",
    "is_mock_mode_enabled",
]
