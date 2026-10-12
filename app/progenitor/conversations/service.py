"""Chat transcripts cached in the LangGraph store, refreshed only when read.

Each chat has a small metadata entry (``progenitor.conversations.meta``: the
newest root checkpoint id rendered, a render key of format version, user nick,
timezone and raw chat title, plus dates) and its rendered text in a separate
row (``progenitor.conversations.logs``), so listing never loads any text.

- ``list_files`` never checks chats for new turns. It renders only chats it has
  never seen (or that had no messages yet), so a chat's file appears once it
  has a first message. Renames and archiving apply at once because names and
  visibility come from the backend's session documents on every call.
- ``read`` checks that one chat's newest checkpoint and re-renders if a turn
  landed or the render key changed, so ``read_file`` is always current.
- ``contents`` (grep) searches the cached text as-is; only entries with an
  outdated render key are re-rendered. New turns in other chats show up once
  something reads them (or, later, once a turn-finished hook calls ``refresh``).

Entries for archived or deleted chats are pruned on listing.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .history import latest_checkpoint_id, load_conversation
from .sessions import display_title
from .transcript import DEFAULT_USER_NICK, FORMAT_VERSION, format_log, render_transcript

_MAX_CONCURRENT_RENDERS = 4
_SLUG_CHARS = 40


@dataclass(frozen=True)
class TranscriptFile:
    name: str
    thread_id: str


@dataclass(frozen=True)
class _Settings:
    nick: str
    tz_name: str


class ConversationTranscripts:
    def __init__(self, state_manager: Any, checkpointer: Any) -> None:
        self._sm = state_manager
        self._checkpointer = checkpointer
        self._renders = asyncio.Semaphore(_MAX_CONCURRENT_RENDERS)

    async def list_files(self) -> list[TranscriptFile]:
        """Every non-archived chat with messages, newest activity first."""
        sessions, entries, settings = await self._snapshot()
        listed: list[tuple[str, TranscriptFile]] = []
        for session in sessions:
            entry = entries.get(session["thread_id"])
            if entry is None or entry.get("empty"):
                continue
            name = _file_name(session, entry, settings)
            listed.append(
                (str(entry.get("last_at") or ""), TranscriptFile(name, session["thread_id"]))
            )
        listed.sort(key=lambda pair: pair[0], reverse=True)
        return [file for _, file in listed]

    async def read(self, name: str) -> str | None:
        """The named transcript, re-rendered first if its chat changed; ``None`` if unknown."""
        sessions, entries, settings = await self._snapshot()
        for session in sessions:
            entry = entries.get(session["thread_id"])
            if entry is None or entry.get("empty") or _file_name(session, entry, settings) != name:
                continue
            meta, content = await self.refresh(session, entry=entry, settings=settings)
            if meta.get("empty"):
                return None
            return content if content is not None else await self._text(session, settings)
        return None

    async def contents(self, include: Callable[[str], bool] | None = None) -> dict[str, str]:
        """Cached text of every transcript whose file name ``include`` accepts (all if None).

        Filtering by name happens before any text is loaded or re-rendered.
        """
        sessions, entries, settings = await self._snapshot()
        selected: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for session in sessions:
            entry = entries.get(session["thread_id"])
            if entry is None or entry.get("empty"):
                continue
            name = _file_name(session, entry, settings)
            if include is None or include(name):
                selected.append((name, session, entry))

        async def _current(session: dict[str, Any], entry: dict[str, Any]) -> str | None:
            if entry.get("render_key") == _render_key(session, settings):
                return await self._text(session, settings)
            meta, content = await self.refresh(
                session, entry=entry, settings=settings, check_tip=False
            )
            return None if meta.get("empty") else content

        texts = await asyncio.gather(*(_current(session, entry) for _, session, entry in selected))
        return {
            name: text
            for (name, _, _), text in zip(selected, texts, strict=True)
            if text is not None
        }

    async def refresh(
        self,
        session: dict[str, Any],
        *,
        entry: dict[str, Any] | None = None,
        settings: _Settings | None = None,
        check_tip: bool = True,
    ) -> tuple[dict[str, Any], str | None]:
        """Re-render one chat when its newest checkpoint or render key changed.

        Returns the metadata entry and, when it re-rendered, the new text
        (``None`` means the cached text is still current). With
        ``check_tip=False`` the cached checkpoint id is trusted (no
        checkpointer round trip) and only a render-key change re-renders.
        No locking: two concurrent renders of one chat only duplicate work,
        and a stale save is replaced on the next read's checkpoint check.
        """
        thread_id = session["thread_id"]
        settings = settings or await self._settings()
        key = _render_key(session, settings)
        tip = (entry or {}).get("checkpoint_id")
        if check_tip or entry is None:
            tip = await latest_checkpoint_id(self._checkpointer, thread_id)
        if (
            entry is not None
            and entry.get("checkpoint_id") == tip
            and entry.get("render_key") == key
        ):
            return entry, None
        async with self._renders:
            meta, content = await self._render(session, settings, key)
        await self._sm.save_conversation_transcript(thread_id, meta, content)
        return meta, content

    async def _text(self, session: dict[str, Any], settings: _Settings) -> str | None:
        """Cached text for a current entry; re-renders if the text row is missing."""
        text = await self._sm.get_conversation_log(session["thread_id"])
        if text is not None:
            return text
        meta, content = await self.refresh(session, settings=settings)
        return None if meta.get("empty") else content

    async def _render(
        self, session: dict[str, Any], settings: _Settings, key: str
    ) -> tuple[dict[str, Any], str | None]:
        thread_id = session["thread_id"]
        conversation = await load_conversation(self._checkpointer, thread_id)
        if conversation is None:
            return {"empty": True, "checkpoint_id": None, "render_key": key}, None
        channel = thread_id[:8]
        transcript = render_transcript(
            conversation.root,
            subagents=conversation.subagents,
            channel=channel,
            user_nick=settings.nick,
        )
        if not transcript.lines:
            empty = {"empty": True, "checkpoint_id": conversation.checkpoint_id, "render_key": key}
            return empty, None
        stamped = [line.at for line in transcript.lines if line.at]
        content = format_log(
            transcript,
            channel=channel,
            user_nick=settings.nick,
            title=display_title(str(session.get("title") or ""), transcript.first_user_text),
            topic_set=_parse_iso(session.get("created_at")),
            tz=_zone(settings.tz_name),
            tz_name=settings.tz_name,
        )
        meta = {
            "empty": False,
            "checkpoint_id": conversation.checkpoint_id,
            "render_key": key,
            "first_user_text": transcript.first_user_text,
            "started_at": stamped[0].isoformat() if stamped else None,
            "last_at": stamped[-1].isoformat() if stamped else None,
        }
        return meta, content

    async def _snapshot(self) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], _Settings]:
        """Live sessions, their cache entries (unseen ones rendered), and render settings."""
        sessions, entries, settings = await asyncio.gather(
            self._sm.list_chat_sessions(),
            self._sm.list_conversation_transcripts(),
            self._settings(),
        )
        live = [session for session in sessions if not session.get("archived")]
        live_ids = {session["thread_id"] for session in live}
        for thread_id in entries.keys() - live_ids:
            await self._sm.delete_conversation_transcript(thread_id)
        unseen = [
            session
            for session in live
            if session["thread_id"] not in entries or entries[session["thread_id"]].get("empty")
        ]
        rendered = await asyncio.gather(
            *(
                self.refresh(session, entry=entries.get(session["thread_id"]), settings=settings)
                for session in unseen
            )
        )
        merged = {thread_id: entries[thread_id] for thread_id in entries.keys() & live_ids}
        merged.update(
            {
                session["thread_id"]: meta
                for session, (meta, _) in zip(unseen, rendered, strict=True)
            }
        )
        return live, merged, settings

    async def _settings(self) -> _Settings:
        preferences = await self._sm.get_preferences_config()
        nick = "".join(str(preferences.get("display_name") or "").split()) or DEFAULT_USER_NICK
        return _Settings(nick=nick, tz_name=str(preferences.get("timezone") or "UTC"))


def _render_key(session: dict[str, Any], settings: _Settings) -> str:
    return f"{FORMAT_VERSION}|{settings.nick}|{settings.tz_name}|{session.get('title') or ''}"


def _file_name(session: dict[str, Any], entry: dict[str, Any], settings: _Settings) -> str:
    """``<date>_<title-slug>_<id8>.txt``: date in the user's timezone, title as the sidebar shows it."""
    title = display_title(str(session.get("title") or ""), str(entry.get("first_user_text") or ""))
    started = _parse_iso(entry.get("started_at"))
    date = f"{started.astimezone(_zone(settings.tz_name)):%Y-%m-%d}" if started else "undated"
    return f"{date}_{_slug(title)}_{session['thread_id'][:8]}.txt"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:_SLUG_CHARS].rstrip("-") or "chat"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
