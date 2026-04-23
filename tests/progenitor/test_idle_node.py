"""Tests for the idle node."""

from types import SimpleNamespace

import pytest

from app.progenitor.nodes.idle import create_node


@pytest.mark.asyncio
async def test_idle_node_resets_cycle_and_queue_metrics():
    node = create_node(SimpleNamespace(), state_manager=None)

    result = await node(
        {
            "cycle_count": 3,
            "actions_this_cycle": 4,
            "pending_messages_in_queue": 7,
            "input_wait_seconds": 12.5,
        }
    )

    assert result == {
        "pending_response": None,
        "actions_this_cycle": 0,
        "pending_messages_in_queue": 0,
        "input_wait_seconds": 0.0,
    }


@pytest.mark.asyncio
async def test_idle_node_prompts_on_interval_and_resets_metrics():
    node = create_node(
        SimpleNamespace(runtime=SimpleNamespace(idle_prompt_interval_cycles=10)),
        state_manager=None,
    )

    result = await node({"cycle_count": 10})

    assert result["pending_response"] == "Anything to do?"
    assert result["actions_this_cycle"] == 0
    assert result["pending_messages_in_queue"] == 0
    assert result["input_wait_seconds"] == 0.0


@pytest.mark.asyncio
async def test_idle_node_uses_configured_prompt_interval():
    node = create_node(
        SimpleNamespace(runtime=SimpleNamespace(idle_prompt_interval_cycles=3)),
        state_manager=None,
    )

    result = await node({"cycle_count": 6})

    assert result["pending_response"] == "Anything to do?"
