"""Task-bound ACP result and native Claude transcript reads.

This module deliberately resolves a single persisted task before touching the
Claude transcript mount.  Callers never provide a filesystem path, session id,
or project directory, so this is not a general Claude-config reader.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.acp.models import ACPTaskRecord
from src.progena.documents.kinds import ACP_TASK_KEY_PREFIX

ResultSource = Literal["output", "transcript", "input", "plan", "error"]

_MAX_PAGE_CHARS = 8 * 1024
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CWD_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9]+")


class ACPResultNotFound(LookupError):
    """The requested task or turn is not retained."""


class ACPResultRequestError(ValueError):
    """The request cursor or source is malformed."""


def _cursor_offset(source: str, cursor: str | None) -> int:
    if cursor is None:
        return 0
    prefix, separator, value = cursor.partition(":")
    if prefix != source or not separator or not value.isdecimal():
        raise ACPResultRequestError(f"Invalid cursor for ACP {source} result")
    return int(value)


def _transcript_cursor(cursor: str | None, start: int, end: int) -> tuple[int, int]:
    if cursor is None:
        return start, 0
    pieces = cursor.split(":")
    if (
        len(pieces) != 3
        or pieces[0] != "transcript"
        or not pieces[1].isdecimal()
        or not pieces[2].isdecimal()
    ):
        raise ACPResultRequestError("Invalid cursor for ACP transcript result")
    line, offset = int(pieces[1]), int(pieces[2])
    if line < start or line > end:
        raise ACPResultRequestError("ACP transcript cursor is outside the selected turn")
    return line, offset


def _page_text(source: str, content: str, cursor: str | None, limit: int) -> dict[str, Any]:
    offset = _cursor_offset(source, cursor)
    if offset > len(content):
        raise ACPResultRequestError("ACP result cursor is beyond the available content")
    end = min(offset + limit, len(content))
    return {
        "content": content[offset:end],
        "next_cursor": f"{source}:{end}" if end < len(content) else None,
        "truncated": end < len(content),
    }


def _json_content(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _valid_session_id(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    return str(parsed) if str(parsed) == value.lower() else None


def _project_directory(cwd: str | None) -> str | None:
    if not isinstance(cwd, str) or not cwd.startswith("/"):
        return None
    parts = Path(cwd).parts
    if ".." in parts or "\x00" in cwd:
        return None
    encoded = _CWD_COMPONENT_PATTERN.sub("-", cwd)
    return encoded or None


class NativeClaudeTranscriptReader:
    """Read only a verified task/session range beneath the mounted transcript root."""

    def __init__(self, root: Path | str | None = None) -> None:
        configured = root or os.environ.get(
            "PROGENA_CLAUDE_TRANSCRIPT_ROOT", "/var/lib/progena/claude/projects"
        )
        self._root = Path(configured)

    def start_turn(self, session_id: str | None, cwd: str | None) -> dict[str, Any] | None:
        """Capture the native line boundary before a Claude prompt starts."""
        session = _valid_session_id(session_id)
        project = _project_directory(cwd)
        if session is None or project is None:
            return None
        path = self._path(session, project, require_file=False)
        if path is None:
            return None
        line_start = self._line_count(path)
        return {
            "provider": "claude_native",
            "session_id": session,
            "line_start": line_start,
        }

    def finish_turn(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> dict[str, Any] | None:
        """Seal a turn range only after verifying the file/session/cwd correlation."""
        if not isinstance(reference, dict) or reference.get("provider") != "claude_native":
            return None
        session = _valid_session_id(reference.get("session_id"))
        project = _project_directory(cwd)
        if session is None or project is None:
            return None
        path = self._path(session, project, require_file=True)
        if path is None or not self._metadata_matches(path, session, cwd):
            return None
        start = reference.get("line_start")
        if not isinstance(start, int) or start < 0:
            return None
        end = self._line_count(path)
        if start > end:
            return None
        return {**reference, "cwd": cwd, "line_start": start, "line_end": end}

    def final_output(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> tuple[str | None, int | None]:
        """Return the final main-assistant structured text for one sealed turn."""
        resolved = self._resolve_reference(reference, cwd=cwd)
        if resolved is None:
            return None, None
        path, start, end = resolved
        final_line: int | None = None
        final_row: dict[str, Any] | None = None
        for line_number, row in self._rows(path, start, end):
            if self._is_main_assistant(row):
                final_line = line_number
                final_row = row
        return self._assistant_text(final_row), final_line

    def transcript_page(
        self,
        reference: dict[str, Any] | None,
        *,
        cwd: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any] | None:
        """Return an exact, bounded raw-JSONL range for a sealed native turn."""
        resolved = self._resolve_reference(reference, cwd=cwd)
        if resolved is None:
            return None
        path, start, end = resolved
        line_cursor, character_cursor = _transcript_cursor(cursor, start, end)
        pieces: list[str] = []
        remaining = limit
        next_line = line_cursor
        next_offset = character_cursor
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle):
                if line_number < line_cursor:
                    continue
                if line_number >= end:
                    break
                fragment = raw[character_cursor:] if line_number == line_cursor else raw
                if len(fragment) <= remaining:
                    pieces.append(fragment)
                    remaining -= len(fragment)
                    next_line = line_number + 1
                    next_offset = 0
                    if remaining == 0:
                        break
                    continue
                if remaining:
                    pieces.append(fragment[:remaining])
                next_line = line_number
                next_offset = (character_cursor if line_number == line_cursor else 0) + remaining
                break
        has_more = next_line < end
        return {
            "content": "".join(pieces),
            "next_cursor": f"transcript:{next_line}:{next_offset}" if has_more else None,
            "truncated": has_more,
            "line_start": start,
            "line_end": end,
        }

    def _resolve_reference(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> tuple[Path, int, int] | None:
        if not isinstance(reference, dict) or reference.get("provider") != "claude_native":
            return None
        session = _valid_session_id(reference.get("session_id"))
        project = _project_directory(cwd)
        start = reference.get("line_start")
        end = reference.get("line_end")
        if (
            session is None
            or project is None
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end < start
        ):
            return None
        path = self._path(session, project, require_file=True)
        if path is None or not self._metadata_matches(path, session, cwd):
            return None
        return path, start, min(end, self._line_count(path))

    def _path(self, session: str, project: str, *, require_file: bool) -> Path | None:
        try:
            root = self._root.resolve(strict=True)
        except OSError:
            return None
        project_path = root / project
        candidate = project_path / f"{session}.jsonl"
        try:
            if project_path.is_symlink() or (require_file and candidate.is_symlink()):
                return None
            resolved = candidate.resolve(strict=require_file)
        except OSError:
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        if require_file and not resolved.is_file():
            return None
        return resolved

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    @staticmethod
    def _metadata_matches(path: Path, session: str, cwd: str | None) -> bool:
        if not isinstance(cwd, str):
            return False
        metadata_seen = False
        try:
            with path.open("r", encoding="utf-8") as handle:
                for index, raw in enumerate(handle):
                    if index >= 512:
                        break
                    try:
                        row = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    metadata_seen = row.get("sessionId") == session and row.get("cwd") == cwd
                    if metadata_seen:
                        return True
        except OSError:
            return False
        return False

    @staticmethod
    def _rows(path: Path, start: int, end: int):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle):
                    if line_number < start:
                        continue
                    if line_number >= end:
                        break
                    try:
                        yield line_number, json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return

    @staticmethod
    def _is_main_assistant(row: Any) -> bool:
        if (
            not isinstance(row, dict)
            or row.get("type") != "assistant"
            or row.get("isSidechain") is True
        ):
            return False
        message = row.get("message")
        return isinstance(message, dict) and message.get("role") == "assistant"

    @classmethod
    def _assistant_text(cls, row: Any) -> str | None:
        if not cls._is_main_assistant(row):
            return None
        message = row["message"]
        content = message.get("content")
        if not isinstance(content, list):
            return None
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts) if texts else None


def _record(store: BackendDocumentStore, task_id: str) -> ACPTaskRecord:
    if not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ACPResultNotFound(task_id)
    document = store.get_document(PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", None)
    if document is None or not isinstance(document.payload, dict):
        raise ACPResultNotFound(task_id)
    return ACPTaskRecord.model_validate({"task_id": task_id, **document.payload})


def _turn_entry(record: ACPTaskRecord, turn: int | None) -> tuple[int, dict[str, Any], bool]:
    selected_turn = record.turn if turn is None else turn
    if selected_turn < 0:
        raise ACPResultNotFound(str(selected_turn))
    if selected_turn == record.turn:
        return selected_turn, {}, True
    for entry in reversed(record.history):
        if isinstance(entry, dict) and entry.get("turn") == selected_turn:
            return selected_turn, entry, False
    raise ACPResultNotFound(str(selected_turn))


def _preview(value: Any, limit: int = 256) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _metadata(
    record: ACPTaskRecord, turn: int, entry: dict[str, Any], current: bool
) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "turn": turn,
        "status": _preview(record.status_value if current else entry.get("status"), 64),
        "stop_reason": _preview(record.stop_reason if current else entry.get("stop_reason")),
        "last_error": _preview(record.last_error if current else entry.get("last_error")),
        "failure_kind": _preview(record.failure_kind if current else entry.get("failure_kind"), 32),
        "result_ref": {"task_id": record.task_id, "turn": turn},
    }


def _evidence_cwd(record: ACPTaskRecord, evidence: Any) -> str | None:
    if isinstance(evidence, dict) and isinstance(evidence.get("cwd"), str):
        return evidence["cwd"]
    return record.cwd


def get_acp_result(
    store: BackendDocumentStore,
    task_id: str,
    *,
    source: ResultSource,
    turn: int | None = None,
    cursor: str | None = None,
    limit: int = 8192,
    transcript_reader: NativeClaudeTranscriptReader | None = None,
) -> dict[str, Any]:
    """Read one explicit, paginated detail source for one persisted ACP task."""
    if source not in {"output", "transcript", "input", "plan", "error"}:
        raise ACPResultRequestError("Unknown ACP result source")
    if not 1 <= limit <= _MAX_PAGE_CHARS:
        raise ACPResultRequestError(f"ACP result limit must be between 1 and {_MAX_PAGE_CHARS}")
    record = _record(store, task_id)
    selected_turn, entry, current = _turn_entry(record, turn)
    payload = _metadata(record, selected_turn, entry, current) | {"source": source}

    if source == "error":
        error = record.last_error if current else entry.get("last_error")
        if not isinstance(error, str):
            payload["availability"] = "unavailable"
            payload.update(_page_text(source, "", cursor, limit))
            return payload
        payload["availability"] = "stored"
        payload.update(_page_text(source, error, cursor, limit))
        return payload

    if source == "output":
        output = record.output if current else entry.get("output")
        evidence = entry.get("evidence") if not current else record.evidence
        payload["availability"] = (
            "native"
            if isinstance(evidence, dict) and evidence.get("final_output_available", True)
            else "stored"
        )
        if isinstance(evidence, dict) and evidence.get("output_truncated"):
            payload["availability"] = "partial"
        if not isinstance(output, str):
            payload["availability"] = "unavailable"
            payload.update(_page_text(source, "", cursor, limit))
            return payload
        payload.update(_page_text(source, output, cursor, limit))
        return payload

    if source == "input":
        request = record.pending_input if current else None
        if request is None:
            requests = [
                item
                for item in record.input_history
                if isinstance(item, dict) and item.get("turn") == selected_turn
            ]
            request = requests[-1] if requests else None
        if request is None:
            payload["availability"] = "unavailable"
            payload.update(_page_text(source, "", cursor, limit))
            return payload
        payload["availability"] = "stored"
        payload["encoding"] = "json"
        payload.update(_page_text(source, _json_content(request), cursor, limit))
        return payload

    if source == "plan":
        plan = record.progress.get("plan") if current else entry.get("plan")
        if not isinstance(plan, list):
            payload["availability"] = "unavailable"
            payload.update(_page_text(source, "", cursor, limit))
            return payload
        payload["availability"] = "stored"
        payload["encoding"] = "json"
        payload.update(_page_text(source, _json_content(plan), cursor, limit))
        return payload

    evidence = entry.get("evidence") if not current else record.evidence
    if transcript_reader is None:
        payload["availability"] = "unavailable"
        payload["detail"] = "Native transcript reader is unavailable in this process"
        payload.update(_page_text(source, "", cursor, limit))
        return payload
    transcript = transcript_reader.transcript_page(
        evidence if isinstance(evidence, dict) else None,
        cwd=_evidence_cwd(record, evidence),
        cursor=cursor,
        limit=limit,
    )
    if transcript is None:
        payload["availability"] = "unavailable"
        payload.update(_page_text(source, "", cursor, limit))
        return payload
    payload["availability"] = "native"
    payload.update(transcript)
    return payload
