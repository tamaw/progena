"""Current-session state helpers."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from .models import ConversationMessage


async def get_current_state(
    *,
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    current_state_path: Path,
) -> dict[str, Any]:
    if "current_state" in state_cache:
        return copy.deepcopy(cast(dict[str, Any], state_cache["current_state"]))
    async with file_lock("current_state"):
        data = await read_json(current_state_path) or {}
        state_cache["current_state"] = data
        return copy.deepcopy(data)


async def save_current_state(
    *,
    state: dict[str, Any],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    current_state_path: Path,
) -> None:
    state_copy = copy.deepcopy(state)
    async with file_lock("current_state"):
        await atomic_write_json(current_state_path, state_copy)
        state_cache["current_state"] = state_copy


async def update_current_state(
    *,
    updates: dict[str, Any],
    state_cache: dict[str, Any],
    file_lock: Callable[[str], Any],
    read_json: Callable[[Path], Awaitable[Any]],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
    current_state_path: Path,
) -> None:
    async with file_lock("current_state"):
        state = copy.deepcopy(cast(dict[str, Any], state_cache.get("current_state", {})))
        if not state:
            state = await read_json(current_state_path) or {}
        state.update(updates)
        await atomic_write_json(current_state_path, state)
        state_cache["current_state"] = state


async def get_messages(**kwargs) -> list[ConversationMessage]:
    state = await get_current_state(**kwargs)
    messages_data = state.get("messages", [])
    return [ConversationMessage(**msg) if isinstance(msg, dict) else msg for msg in messages_data]


async def save_messages(*, messages: list[ConversationMessage], **kwargs) -> None:
    state = await get_current_state(**kwargs)
    state["messages"] = [
        msg.model_dump() if hasattr(msg, "model_dump") else msg for msg in messages
    ]
    await save_current_state(state=state, **kwargs)


async def add_message(*, message: ConversationMessage, **kwargs) -> None:
    messages = await get_messages(**kwargs)
    messages.append(message)
    await save_messages(messages=messages, **kwargs)


async def get_tool_calls(**kwargs) -> list[dict[str, Any]]:
    state = await get_current_state(**kwargs)
    return cast(list[dict[str, Any]], state.get("tool_calls", []))


async def add_tool_call(*, tool_call: dict[str, Any], **kwargs) -> None:
    state = await get_current_state(**kwargs)
    tool_calls = state.get("tool_calls", [])
    tool_calls.append(tool_call)
    state["tool_calls"] = tool_calls
    await save_current_state(state=state, **kwargs)


async def get_attachments(**kwargs) -> list[dict[str, Any]]:
    state = await get_current_state(**kwargs)
    return cast(list[dict[str, Any]], state.get("attachments", []))


async def add_attachment(*, attachment: dict[str, Any], **kwargs) -> None:
    state = await get_current_state(**kwargs)
    attachments = state.get("attachments", [])
    attachments.append(attachment)
    state["attachments"] = attachments
    await save_current_state(state=state, **kwargs)
