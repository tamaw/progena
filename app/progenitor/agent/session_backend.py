"""DeepAgent backend exposing chat sessions as documents under ``/sessions/``.

Each non-archived session is a file::

    <date>_<title-slug>_<id8>.md

where ``<date>`` is the day the chat started in the user's timezone.
The file text is YAML front matter (all payload fields except ``content``,
plus ``display_title``) followed by a blank line and the IRC-style
``content``.

Only the type's ``writable`` fields (``title``, ``pinned``, ``archived``)
may be changed; any other change is rejected.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileInfo,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import create_file_data, grep_matches_from_files, slice_read_response
from wcmatch import fnmatch

from ..sessions.titles import display_title
from ..state.manager import StateManager

logger = logging.getLogger(__name__)

_READ_ONLY_BODY = "The session body cannot be changed; only the front matter is editable."
_DISALLOWED_DELETE = "Sessions cannot be deleted via the filesystem."
_SESSION_WRITABLE: frozenset[str] = frozenset({"title", "pinned", "archived"})
_SLUG_CHARS = 40


class SessionDirectoryBackend:
    """Async backend over session documents, read-write on front matter only."""

    _ASYNC_ONLY = "SessionDirectoryBackend is async-only; use the async file tools."

    def __init__(self, state_manager: StateManager) -> None:
        self._sm = state_manager

    @staticmethod
    def _name(file_path: str) -> str:
        return file_path.lstrip("/").removeprefix("sessions/")

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        name = self._name(file_path)
        session = await self._find_session_by_name(name)
        if session is None:
            return ReadResult(error=f"File '{file_path}' not found")
        text = self._format_file(session)
        file_data = create_file_data(text)
        sliced = slice_read_response(file_data, offset, limit)
        if isinstance(sliced, ReadResult):
            return sliced
        return ReadResult(
            file_data=FileData(content=sliced, encoding=file_data.get("encoding", "utf-8"))
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        name = self._name(file_path)
        session = await self._find_session_by_name(name)
        if session is None:
            return WriteResult(error=f"File '{file_path}' not found")
        return await self._apply_edit(session, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        name = self._name(file_path)
        session = await self._find_session_by_name(name)
        if session is None:
            return EditResult(error=f"File '{file_path}' not found")
        current = self._format_file(session)
        if replace_all:
            proposed = current.replace(old_string, new_string)
        else:
            proposed = current.replace(old_string, new_string, 1)
        if proposed == current:
            return EditResult(error="The edit did not change the file")
        result = await self._apply_edit(session, proposed)
        if result.error:
            return EditResult(error=result.error)
        return EditResult()

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_DISALLOWED_DELETE)

    async def als(self, path: str) -> LsResult:
        sessions = await self._list_sessions()
        return LsResult(
            entries=[FileInfo(path=f"/{self._file_name(s)}", is_dir=False) for s in sessions]
        )

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None
    ) -> GrepResult:
        target = self._name(path or "/").rstrip("/")

        def include(name: str) -> bool:
            if target and name != target:
                return False
            return not glob or fnmatch.fnmatch(name, glob, flags=fnmatch.BRACE)

        sessions = await self._list_sessions()
        files: dict[str, FileData] = {}
        for session in sessions:
            name = self._file_name(session)
            if include(name):
                files[f"/{name}"] = create_file_data(self._format_file(session))
        return grep_matches_from_files(files, pattern, path or "/", glob)

    # -- internal helpers ---------------------------------------------------

    async def _list_sessions(self) -> list[dict[str, Any]]:
        rows = await self._sm.list_sessions()
        preferences = await self._sm.get_preferences_config()
        tz_name = str(preferences.get("timezone") or "UTC")
        sessions: list[dict[str, Any]] = []
        for payload in rows:
            if not payload["archived"]:
                sessions.append({**payload, "_tz": tz_name})
        sessions.sort(key=lambda s: s["last_activity"], reverse=True)
        return sessions

    async def _find_session_by_name(self, name: str) -> dict[str, Any] | None:
        sessions = await self._list_sessions()
        for session in sessions:
            if self._file_name(session) == name:
                return session
        return None

    async def _apply_edit(self, session: dict[str, Any], proposed_text: str) -> WriteResult:
        parsed = self._parse_file(proposed_text)
        if parsed is None:
            return WriteResult(error="Invalid file format: expected YAML front matter")

        front, body = parsed
        current_text = self._format_file(session)
        current_parsed = self._parse_file(current_text)
        assert current_parsed is not None
        _current_front, current_body = current_parsed

        if body != current_body:
            return WriteResult(error=_READ_ONLY_BODY)

        changed: dict[str, Any] = {}
        for key, value in front.items():
            if key == "display_title":
                continue
            if session.get(key) != value:
                if key not in _SESSION_WRITABLE:
                    return WriteResult(
                        error=f"Field '{key}' is not editable. Allowed fields: {', '.join(sorted(_SESSION_WRITABLE))}"
                    )
                changed[key] = value

        if not changed:
            return WriteResult()

        thread_id = session["thread_id"]
        merged = await self._sm.merge_session(thread_id, changed)
        if not merged:
            return WriteResult(error="Session no longer exists")
        return WriteResult()

    def _format_file(self, session: dict[str, Any]) -> str:
        front: dict[str, Any] = {
            k: v
            for k, v in session.items()
            if k not in ("content", "thread_id") and not k.startswith("_")
        }
        front["display_title"] = display_title(session["title"], session["first_message"])
        yaml_text = yaml.safe_dump(
            front, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
        return f"---\n{yaml_text}---\n\n{session['content']}"

    def _parse_file(self, text: str) -> tuple[dict[str, Any], str] | None:
        if not text.startswith("---\n"):
            return None
        end = text.find("\n---", 4)
        if end == -1:
            return None
        try:
            front = yaml.safe_load(text[4:end]) or {}
        except Exception:
            return None
        body = text[end + 4 :].lstrip("\n")
        return front, body

    def _file_name(self, session: dict[str, Any]) -> str:
        title = display_title(session["title"], session["first_message"])
        created = datetime.fromisoformat(session["created_at"])
        date = f"{created.astimezone(self._zone(session['_tz'])):%Y-%m-%d}"
        return f"{date}_{self._slug(title)}_{session['thread_id'][:8]}.md"

    @staticmethod
    def _zone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @staticmethod
    def _slug(text: str) -> str:
        return (
            re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:_SLUG_CHARS].rstrip("-") or "chat"
        )
