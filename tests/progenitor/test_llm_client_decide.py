"""Tests for Progenitor LLM decide parsing."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.llm.client import LLMClient


@pytest.mark.asyncio
async def test_decide_raises_on_invalid_json_response():
    """Invalid decision JSON should surface as an error instead of defaulting."""
    client = LLMClient.__new__(LLMClient)
    client.prompts = MagicMock()
    client.prompts.get_decide_prompt.return_value = "decide"
    client.chat = AsyncMock(return_value="not-json")

    with pytest.raises(ValueError, match="Invalid decision JSON"):
        await LLMClient.decide(
            client,
            user_input="build something",
            conversation_history=[],
            tool_context="",
        )
