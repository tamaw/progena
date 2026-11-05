"""Render a thread's transcript and preview and merge them into the session document."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.messages import AIMessage, HumanMessage

from src.progena.settings.preferences import agent_display_name

from .history import Conversation, load_conversation
from .messages import extract_message_text
from .titles import DEFAULT_USER_NICK, display_title, session_preview
from .transcript import format_log, render_transcript

logger = logging.getLogger(__name__)


def latest_message_preview(conversation: Conversation) -> str:
    """The newest non-empty user or assistant message of the root thread, as ``preview``."""
    for message in reversed(conversation.root.messages):
        if not isinstance(message, HumanMessage | AIMessage):
            continue
        preview = session_preview(extract_message_text(message) or "")
        if preview:
            return preview
    return ""


async def render_session_fields(
    checkpointer: Any,
    thread_id: str,
    *,
    title: str,
    preferences: dict[str, Any],
) -> dict[str, str] | None:
    """The thread's IRC-style ``content`` and list ``preview``; ``None`` if it has no lines."""
    conversation = await load_conversation(checkpointer, thread_id)
    if conversation is None:
        return None
    nick = "".join(str(preferences.get("display_name") or "").split()) or DEFAULT_USER_NICK
    agent_nick = "".join(agent_display_name(preferences).split())
    tz_name = str(preferences.get("timezone") or "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    channel = thread_id[:8]
    transcript = render_transcript(
        replace(conversation.root, nick=agent_nick),
        subagents=conversation.subagents,
        channel=channel,
        user_nick=nick,
    )
    if not transcript.lines:
        return None
    content = format_log(
        transcript,
        channel=channel,
        user_nick=nick,
        title=display_title(title, transcript.first_user_text),
        topic_set=None,
        tz=tz,
        tz_name=tz_name,
    )
    return {"content": content, "preview": latest_message_preview(conversation)}


async def merge_session_after_turn(
    thread_id: str,
    checkpointer: Any,
    state_manager: Any,
) -> None:
    """Re-render the thread and atomically merge ``content``/``preview``/``last_activity``.

    Fire-and-forget: exceptions are logged, never raised.
    """
    try:
        session = await state_manager.get_session(thread_id)
        if session is None:
            return
        fields = await render_session_fields(
            checkpointer,
            thread_id,
            title=session["title"],
            preferences=await state_manager.get_preferences_config(),
        )
        if fields is None:
            return
        merged = await state_manager.merge_session(
            thread_id,
            {**fields, "last_activity": datetime.now(UTC).isoformat()},
        )
        if not merged:
            logger.debug("Session %s not found for merge", thread_id)
    except Exception:
        logger.warning("Session merge failed for %s", thread_id, exc_info=True)
