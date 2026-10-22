"""Conversation cycle nodes and helpers."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from langgraph.runtime import Runtime

from src.progena.orchestration.nodes.chat import chat_node
from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.nodes.respond import response_node
from src.progena.orchestration.state import ProgenaState
from src.progena.sessions.context import reset_current_thread_id, set_current_thread_id
from src.progena.sessions.messages import get_last_ai_message, summarize_message_schema

logger = logging.getLogger(__name__)


async def isolated_cycle_node(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run one cycle on a temporary thread, loading per-thread state fresh."""
    requested_thread_id = str(state.get("requested_thread_id") or "").strip()
    thread_state = await runtime.context.state_manager.get_current_state(
        thread_id=requested_thread_id
    )
    turn_state = {
        **state,
        "thread_id": requested_thread_id,
        "cycle_count": int(thread_state.get("cycle_count", 0)) + 1,
        "pending_response": None,
        "last_reasoning_trace": None,
    }
    token = set_current_thread_id(requested_thread_id)
    try:
        updates = await cycle_node(cast(ProgenaState, turn_state), runtime)
    finally:
        reset_current_thread_id(token)
    await runtime.context.state_manager.update_current_state(
        {
            "cycle_count": turn_state["cycle_count"],
            "pending_response": updates.get("pending_response"),
            "last_reasoning_trace": updates.get("last_reasoning_trace"),
        },
        thread_id=requested_thread_id,
    )
    return {
        key: value
        for key, value in updates.items()
        if key in {"last_cycle_response", "last_reasoning_trace", "pending_response", "cycle_error"}
    }


async def cycle_node(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run one conversation cycle and convert failures into cycle_error."""
    try:
        return await _run_cycle(state, runtime)
    except Exception as exc:
        logger.error("Heartbeat node failed: %s", exc, exc_info=True)
        return {"cycle_error": str(exc)}


async def _run_cycle(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    # Pull the active state fields we need for this turn.
    cycle_num = int(state.get("cycle_count", 0) or 0)
    source = str(state.get("cycle_source") or "unknown")
    current_input = str(state.get("current_input") or "").strip()
    input_metadata = state.get("current_input_metadata")
    requested_thread_id = str(state.get("requested_thread_id") or "").strip() or None
    effective_thread_id = str(requested_thread_id or state.get("thread_id") or "progena_main")

    logger.info("Running graph cycle %d (source: %s)", cycle_num, source)

    updates: dict[str, Any] = {"last_cycle_response": None}
    working_state = dict(state)
    if current_input:
        # Run the DeepAgent graph only when there is user input to process.
        token = set_current_thread_id(effective_thread_id)
        try:
            chat_updates = await chat_node(
                cast(ProgenaState, {**working_state, "thread_id": effective_thread_id}),
                runtime,
            )
        finally:
            reset_current_thread_id(token)
        working_state.update(chat_updates)
        updates.update(chat_updates)

    response_updates = await response_node(cast(ProgenaState, working_state), runtime)
    working_state.update(response_updates)
    updates.update(response_updates)

    response_text = cast(str | None, working_state.get("last_cycle_response"))
    if response_text:
        logger.info("Extracted AI response (%d chars)", len(response_text))
        if isinstance(input_metadata, dict) and bool(input_metadata.get("notify_on_completion")):
            event_type = (
                str(input_metadata.get("completion_event_type") or "").strip() or "agent_message"
            )
            await runtime.context.queues.push_event(
                event_type,
                response_text,
                {
                    "source": "heartbeat_input_completion",
                    "thread_id": effective_thread_id,
                },
            )

    reasoning_trace = cast(str | None, working_state.get("last_reasoning_trace"))
    if reasoning_trace:
        logger.info("Extracted reasoning trace (%d chars)", len(reasoning_trace))
    else:
        last_ai_msg = get_last_ai_message(working_state.get("messages"))
        if last_ai_msg is not None and runtime.context.reasoning_enabled:
            logger.info(
                "No reasoning trace found in assistant message schema: %s",
                json.dumps(summarize_message_schema(last_ai_msg), default=str),
            )

    return updates
