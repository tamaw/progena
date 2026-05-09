"""Conversation cycle nodes and helpers."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, cast

from langgraph.runtime import Runtime

from ..runtime.messages import get_last_ai_message, summarize_message_schema
from ..state.models import ProgenitorState
from .chat import chat_node
from .context import HeartbeatContext
from .respond import response_node

logger = logging.getLogger(__name__)

_CONVERSATION_SNAPSHOT_KEYS = (
    "thread_id",
    "messages",
    "attachments",
    "tool_calls",
    "current_input",
    "current_input_role",
    "current_input_metadata",
    "pending_response",
    "last_reasoning_trace",
)


def snapshot_conversation_state(state: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the active conversation state before an isolated run."""

    return {key: copy.deepcopy(state.get(key)) for key in _CONVERSATION_SNAPSHOT_KEYS}


def restore_conversation_state(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    """Restore the conversation state that was saved before isolation."""

    for key, value in snapshot.items():
        state[key] = value


async def isolated_cycle_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run one cycle on a temporary thread, then put the main thread back."""
    live_state = runtime.context.live_state
    requested_thread_id = str(state.get("requested_thread_id") or "").strip()
    snapshot = snapshot_conversation_state(live_state)
    try:
        # Clear transient content so the isolated thread starts clean.
        live_state["thread_id"] = requested_thread_id
        live_state["messages"] = []
        live_state["attachments"] = []
        live_state["tool_calls"] = []
        live_state["pending_response"] = None
        live_state["last_reasoning_trace"] = None
        updates = await cycle_node(cast(ProgenitorState, {**live_state, **state}), runtime)
    finally:
        restore_conversation_state(live_state, snapshot)

    return {
        key: value
        for key, value in updates.items()
        if key in {"last_cycle_response", "last_reasoning_trace", "pending_response", "cycle_error"}
    }


async def cycle_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run one conversation cycle and convert failures into cycle_error."""
    try:
        return await _run_cycle(state, runtime)
    except Exception as exc:
        logger.error("Heartbeat node failed: %s", exc, exc_info=True)
        return {"cycle_error": str(exc)}


async def _run_cycle(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    # Pull the active state fields we need for this turn.
    cycle_num = int(state.get("cycle_count", 0) or 0)
    source = str(state.get("cycle_source") or "unknown")
    current_input = str(state.get("current_input") or "").strip()
    requested_thread_id = str(state.get("requested_thread_id") or "").strip() or None
    effective_thread_id = str(requested_thread_id or state.get("thread_id") or "progenitor_main")

    logger.info("Running graph cycle %d (source: %s)", cycle_num, source)

    updates: dict[str, Any] = {"last_cycle_response": None}
    working_state = dict(state)
    if current_input:
        # Run the DeepAgent graph only when there is user input to process.
        chat_updates = await chat_node(
            cast(ProgenitorState, {**working_state, "thread_id": effective_thread_id}),
            runtime,
        )
        working_state.update(chat_updates)
        updates.update(chat_updates)

    response_updates = await response_node(cast(ProgenitorState, working_state), runtime)
    working_state.update(response_updates)
    updates.update(response_updates)

    response_text = cast(str | None, working_state.get("last_cycle_response"))
    if response_text:
        logger.info("Extracted AI response (%d chars)", len(response_text))

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

    live_state = runtime.context.live_state
    current_thread_id = str(live_state.get("thread_id") or working_state.get("thread_id") or "")
    if current_thread_id != effective_thread_id:
        updates.update({"messages": [], "attachments": [], "tool_calls": []})

    return updates
