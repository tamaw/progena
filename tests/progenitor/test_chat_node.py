"""Tests for the chat node."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.state.models import Decision


class _FakeLLMClient:
    async def chat(self, *args, **kwargs) -> str:  # noqa: ANN001
        # Simulate a system tool (e.g., new_conversation) writing state_updates that
        # include current_decision=None; chat node must not let this override the
        # Decision it creates for this turn.
        ctx = kwargs.get("system_tool_context") or {}
        updates = ctx.get("state_updates")
        if isinstance(updates, dict):
            updates.update({"current_decision": None, "thread_id": "thread_test"})
        return "ok"


class TestChatNode:
    @pytest.mark.asyncio
    async def test_state_updates_do_not_override_current_decision(self):
        from app.progenitor.nodes import chat

        node = chat.create_node(
            _FakeLLMClient(),
            tool_registry=MagicMock(),
            state_manager=AsyncMock(),
            config=MagicMock(),
        )

        result = await node({"current_input": "new chat and list tools", "messages": []})

        assert isinstance(result.get("current_decision"), Decision)
        assert result["current_decision"].user_response == "ok"
        assert result["thread_id"] == "thread_test"
