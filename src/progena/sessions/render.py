"""Render a thread's transcript and merge it into the session document."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.progena.settings.preferences import agent_display_name

from .history import load_conversation
from .titles import DEFAULT_USER_NICK, display_title
from .transcript import format_log, render_transcript

logger = logging.getLogger(__name__)


async def render_session_content(
    checkpointer: Any,
    thread_id: str,
    *,
    title: str,
    preferences: dict[str, Any],
) -> str | None:
    """The thread's IRC-style transcript as stored in ``content``; ``None`` if it has no lines."""
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
    return format_log(
        transcript,
        channel=channel,
        user_nick=nick,
        title=display_title(title, transcript.first_user_text),
        topic_set=None,
        tz=tz,
        tz_name=tz_name,
    )


async def merge_session_after_turn(
    thread_id: str,
    checkpointer: Any,
    state_manager: Any,
) -> None:
    """Re-render the thread and atomically merge ``content``/``last_activity`` into the session row.

    Fire-and-forget: exceptions are logged, never raised.
    """
    try:
        session = await state_manager.get_session(thread_id)
        if session is None:
            return
        content = await render_session_content(
            checkpointer,
            thread_id,
            title=session["title"],
            preferences=await state_manager.get_preferences_config(),
        )
        if content is None:
            return
        merged = await state_manager.merge_session(
            thread_id,
            {"content": content, "last_activity": datetime.now(UTC).isoformat()},
        )
        if not merged:
            logger.debug("Session %s not found for merge", thread_id)
    except Exception:
        logger.warning("Session merge failed for %s", thread_id, exc_info=True)
