"""Tests for queue-aware intake metadata."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.nodes.intake import QUEUE_WAIT_THRESHOLD_SECONDS, create_node


@pytest.mark.asyncio
async def test_intake_node_adds_queue_metadata_for_delayed_input():
    """Delayed inputs should carry queue timing metadata into the message log."""
    state_manager = MagicMock()
    state_manager.log_conversation = AsyncMock()
    state_manager.save_messages = AsyncMock()
    node = create_node(state_manager)

    result = await node(
        {
            "messages": [],
            "current_input": "hello",
            "current_input_role": "user",
            "current_input_metadata": {"source": "chat"},
            "input_wait_seconds": QUEUE_WAIT_THRESHOLD_SECONDS + 4.3,
            "pending_messages_in_queue": 2,
        }
    )

    message = result["messages"][-1]
    assert message.metadata["queued_wait_seconds"] == 34.3
    assert message.metadata["pending_messages_in_queue"] == 2
    state_manager.log_conversation.assert_awaited_once_with(message)
    state_manager.save_messages.assert_awaited_once_with(result["messages"])


@pytest.mark.asyncio
async def test_intake_node_omits_queue_metadata_when_below_threshold():
    """Fast inputs should not be annotated as queued."""
    state_manager = MagicMock()
    state_manager.log_conversation = AsyncMock()
    state_manager.save_messages = AsyncMock()
    node = create_node(state_manager)

    result = await node(
        {
            "messages": [],
            "current_input": "hello",
            "input_wait_seconds": QUEUE_WAIT_THRESHOLD_SECONDS - 1,
            "pending_messages_in_queue": 0,
        }
    )

    message = result["messages"][-1]
    assert "queued_wait_seconds" not in message.metadata
    assert "pending_messages_in_queue" not in message.metadata
