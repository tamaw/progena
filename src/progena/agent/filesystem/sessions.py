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
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    create_file_data,
    perform_string_replacement,
    slice_read_response,
)

from src.progena.agent.filesystem.base import _AsyncOnlyFileBackendMixin
from src.progena.documents.types import BUILTIN_DOCUMENT_TYPES
from src.progena.sessions.titles import display_title
from src.progena.state.manager import StateManager
from src.progena.utils import normalize_utc_datetime, normalize_utc_timestamp

logger = logging.getLogger(__name__)

_READ_ONLY_BODY = "The session body cannot be changed; only the front matter is editable."
_DISALLOWED_DELETE = "Sessions cannot be deleted via the filesystem."
_SESSION_TYPE = next(item for item in BUILTIN_DOCUMENT_TYPES if item.id == "session")
_SLUG_CHARS = 40


class SessionDirectoryBackend(_AsyncOnlyFileBackendMixin):
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
        replaced = perform_string_replacement(current, old_string, new_string, replace_all)
        if isinstance(replaced, str):
            return EditResult(error=replaced)
        proposed, occurrences = replaced
        if proposed == current:
            return EditResult(error="The edit did not change the file")
        result = await self._apply_edit(session, proposed)
        if result.error:
            return EditResult(error=result.error)
        return EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> WriteResult:
        return WriteResult(error=_DISALLOWED_DELETE)

    async def als(self, path: str) -> LsResult:
        sessions = await self._list_sessions()
        return LsResult(
            entries=[FileInfo(path=f"/{self._file_name(s)}", is_dir=False) for s in sessions]
        )

    # -- internal helpers ---------------------------------------------------

    async def _list_sessions(self) -> list[dict[str, Any]]:
        rows = await self._sm.list_sessions()
        preferences = await self._sm.get_preferences_config()
        tz_name = str(preferences.get("timezone") or "UTC")
        sessions: list[dict[str, Any]] = []
        for payload in rows:
            if not payload["archived"]:
                sessions.append({**payload, "_tz": tz_name})
        sessions.sort(
            key=lambda session: normalize_utc_timestamp(str(session["last_activity"])),
            reverse=True,
        )
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
        current_front, current_body = current_parsed

        if body != current_body:
            return WriteResult(error=_READ_ONLY_BODY)

        changed: dict[str, Any] = {}
        for key, value in front.items():
            if key == "display_title":
                continue
            if current_front.get(key) != value:
                if key not in _SESSION_TYPE.writable_fields:
                    return WriteResult(
                        error=(
                            f"Field '{key}' is not editable. Allowed fields: "
                            f"{', '.join(sorted(_SESSION_TYPE.writable_fields))}"
                        )
                    )
                changed[key] = value

        if not changed:
            return WriteResult()
        error = _SESSION_TYPE.validate_write(changed)
        if error:
            return WriteResult(error=f"Invalid session field value: {error}")

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
        for key in ("created_at", "last_activity"):
            if isinstance(front.get(key), str):
                front[key] = normalize_utc_timestamp(front[key])
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
        created = normalize_utc_datetime(
            datetime.fromisoformat(str(session["created_at"]).replace("Z", "+00:00"))
        )
        assert created is not None
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
