"""Task-bound ACP result and native Claude transcript reads.

This module deliberately resolves a single persisted task before touching the
Claude transcript mount.  Callers never provide a filesystem path, session id,
or project directory, so this is not a general Claude-config reader.
"""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import os
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.acp.models import ACPTaskRecord
from src.progena.documents.kinds import ACP_TASK_KEY_PREFIX

ResultSource = Literal["output", "transcript", "input", "plan", "error"]

_MAX_PAGE_CHARS = 8 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024
_VERIFY_BLOCK_BYTES = 1024 * 1024
_MAX_PROJECT_PREFIX = 200
_TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_JSON_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")


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
    sanitized = "".join(
        character
        if character.isascii() and character.isalnum()
        else "-" * (2 if ord(character) > 0xFFFF else 1)
        for character in cwd
    )
    if not sanitized:
        return None
    if len(sanitized) <= _MAX_PROJECT_PREFIX:
        return sanitized
    encoded = cwd.encode("utf-16-le", "surrogatepass")
    hash_code = 0
    for index in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[index : index + 2], "little")
        hash_code = ((hash_code << 5) - hash_code + unit) & 0xFFFFFFFF
    if hash_code >= 0x80000000:
        hash_code -= 0x100000000
    return f"{sanitized[:_MAX_PROJECT_PREFIX]}-{_base36(abs(hash_code))}"


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result or "0"


class _TranscriptReadError(OSError):
    """The mounted transcript cannot be read as an exact UTF-8 source."""


@dataclass(frozen=True)
class _ResolvedTranscript:
    path: Path
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    block_hashes: tuple[str, ...]


def _text_fragments(
    path: Path,
    *,
    start_byte: int = 0,
    end_byte: int | None = None,
    line: int = 0,
    final: bool = True,
) -> Iterator[tuple[int, int, str]]:
    """Yield decoded fragments from one bounded byte range of a JSONL source."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    column = 0
    try:
        with path.open("rb") as handle:
            handle.seek(start_byte)
            remaining = None if end_byte is None else end_byte - start_byte
            if remaining is not None and remaining < 0:
                raise _TranscriptReadError("invalid transcript byte range")
            while remaining is None or remaining:
                raw = handle.read(
                    _STREAM_CHUNK_BYTES
                    if remaining is None
                    else min(_STREAM_CHUNK_BYTES, remaining)
                )
                if not raw:
                    break
                if remaining is not None:
                    remaining -= len(raw)
                text = decoder.decode(raw)
                position = 0
                while position < len(text):
                    newline = text.find("\n", position)
                    if newline < 0:
                        fragment = text[position:]
                        if fragment:
                            yield line, column, fragment
                            column += len(fragment)
                        break
                    if newline > position:
                        fragment = text[position:newline]
                        yield line, column, fragment
                        column += len(fragment)
                    yield line, column, "\n"
                    line += 1
                    column = 0
                    position = newline + 1
            if final:
                text = decoder.decode(b"", final=True)
                if text:
                    position = 0
                    while position < len(text):
                        newline = text.find("\n", position)
                        if newline < 0:
                            fragment = text[position:]
                            if fragment:
                                yield line, column, fragment
                            break
                        if newline > position:
                            fragment = text[position:newline]
                            yield line, column, fragment
                            column += len(fragment)
                        yield line, column, "\n"
                        line += 1
                        column = 0
                        position = newline + 1
    except UnicodeDecodeError as exc:
        raise _TranscriptReadError("transcript is not valid UTF-8") from exc
    except OSError as exc:
        raise _TranscriptReadError("transcript source is unavailable") from exc


class _JSONString:
    """Decode one JSON string without retaining values the caller does not need."""

    def __init__(self, limit: int | None) -> None:
        self._limit = limit
        self._characters = io.StringIO()
        self._length = 0
        self._overflow = False
        self.valid = True
        self._escaping = False
        self._unicode: list[str] | None = None
        self._high_surrogate: int | None = None

    def feed(self, character: str) -> bool:
        if self._unicode is not None:
            self._unicode.append(character)
            if len(self._unicode) == 4:
                try:
                    codepoint = int("".join(self._unicode), 16)
                except ValueError:
                    self.valid = False
                else:
                    self._append_codepoint(codepoint)
                self._unicode = None
            return False
        if self._escaping:
            self._escaping = False
            if character == "u":
                self._unicode = []
            elif character in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                self._append(
                    {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(
                        character, character
                    )
                )
            else:
                self.valid = False
            return False
        if character == "\\":
            self._escaping = True
            return False
        if character == '"':
            self._flush_surrogate()
            return True
        if ord(character) < 0x20:
            self.valid = False
        self._append(character)
        return False

    def value(self) -> str | None:
        return None if self._overflow else self._characters.getvalue()

    def _append_codepoint(self, codepoint: int) -> None:
        if 0xD800 <= codepoint <= 0xDBFF:
            self._flush_surrogate()
            self._high_surrogate = codepoint
            return
        if self._high_surrogate is not None and 0xDC00 <= codepoint <= 0xDFFF:
            codepoint = 0x10000 + (self._high_surrogate - 0xD800) * 0x400 + (codepoint - 0xDC00)
            self._high_surrogate = None
        else:
            self._flush_surrogate()
        self._append(chr(codepoint))

    def _flush_surrogate(self) -> None:
        if self._high_surrogate is not None:
            self._append(chr(self._high_surrogate))
            self._high_surrogate = None

    def _append(self, character: str) -> None:
        if self._overflow:
            return
        if self._limit is not None and self._length + len(character) > self._limit:
            self._characters = io.StringIO()
            self._length = 0
            self._overflow = True
            return
        self._characters.write(character)
        self._length += len(character)


class _JSONLRow:
    """A bounded JSON event reader for the ACP fields used to authorize a row."""

    _KEYS = {
        "root": {"type", "isSidechain", "sessionId", "cwd", "message"},
        "message": {"role", "id", "content"},
        "content": set(),
        "content_item": {"type", "text"},
        "other": set(),
    }

    def __init__(self, *, collect_text: bool = False) -> None:
        self._stack: list[dict[str, Any]] = []
        self._string: _JSONString | None = None
        self._string_target: tuple[dict[str, Any], str] | None = None
        self._literal: list[str] | None = None
        self._literal_target: tuple[dict[str, Any], str] | None = None
        self._collect_text = collect_text
        self.valid = True
        self.values: dict[str, Any] = {}
        self.texts: list[str] = []

    def feed(self, text: str) -> None:
        for character in text:
            self._feed_character(character)

    def complete(self) -> bool:
        return self.valid and self._string is None and self._literal is None and not self._stack

    def _feed_character(self, character: str) -> None:
        if self._string is not None:
            complete = self._string.feed(character)
            if not self._string.valid:
                self.valid = False
            if complete:
                target = self._string_target
                value = self._string.value()
                self._string = None
                self._string_target = None
                if target is None:
                    self.valid = False
                    return
                frame, key = target
                if frame["state"] == "key":
                    frame["key"] = value if value in self._KEYS[frame["context"]] else None
                    frame["state"] = "colon"
                else:
                    self._store_value(frame, key, value)
                    self._finish_value(frame)
            return
        if self._literal is not None:
            if character in ",}]":
                target = self._literal_target
                value = "".join(self._literal)
                self._literal = None
                self._literal_target = None
                if target is None:
                    self.valid = False
                    return
                if value not in {"true", "false", "null"} and not _JSON_NUMBER_PATTERN.fullmatch(
                    value
                ):
                    self.valid = False
                self._store_value(*target, value)
                self._finish_value(target[0])
                self._feed_character(character)
            elif not character.isspace():
                self._literal.append(character)
            return
        if character.isspace():
            return
        if not self._stack:
            if character != "{":
                self.valid = False
                return
            self._stack.append(
                {"kind": "object", "context": "root", "state": "key", "key": None, "values": {}}
            )
            return
        frame = self._stack[-1]
        state = frame["state"]
        if frame["kind"] == "object":
            if state == "key":
                if character == "}":
                    self._close_container("object")
                elif character == '"':
                    self._start_string(frame, "")
                else:
                    self.valid = False
                return
            if state == "colon":
                if character != ":":
                    self.valid = False
                    return
                frame["state"] = "value"
                return
            if state == "comma":
                if character == ",":
                    frame["state"] = "key"
                elif character == "}":
                    self._close_container("object")
                else:
                    self.valid = False
                return
        else:
            if state == "value_or_end" and character == "]":
                self._close_container("array")
                return
            if state == "comma":
                if character == ",":
                    frame["state"] = "value_or_end"
                elif character == "]":
                    self._close_container("array")
                else:
                    self.valid = False
                return
            if state not in {"value", "value_or_end"}:
                self.valid = False
                return
        self._start_value(frame, character)

    def _start_value(self, frame: dict[str, Any], character: str) -> None:
        key = frame.get("key") if frame["kind"] == "object" else None
        if character == '"':
            self._start_string(frame, key)
        elif character in "[{":
            context = self._child_context(frame, key)
            self._stack.append(
                {
                    "kind": "object" if character == "{" else "array",
                    "context": context,
                    "state": "key" if character == "{" else "value_or_end",
                    "key": None,
                    "values": {},
                }
            )
        else:
            self._literal = [character]
            self._literal_target = (frame, key or "")

    def _start_string(self, frame: dict[str, Any], key: str | None) -> None:
        collect_text = self._collect_text and frame["context"] == "content_item" and key == "text"
        capture = (
            frame["state"] == "key"
            or key in {"type", "isSidechain", "sessionId", "cwd", "role", "id"}
            or collect_text
        )
        self._string = _JSONString(None if collect_text else 4096 if capture else 0)
        self._string_target = (frame, key or "")

    @staticmethod
    def _child_context(frame: dict[str, Any], key: str | None) -> str:
        if frame["context"] == "root" and key == "message":
            return "message"
        if frame["context"] == "message" and key == "content":
            return "content"
        if frame["context"] == "content":
            return "content_item"
        return "other"

    def _store_value(self, frame: dict[str, Any], key: str, value: Any) -> None:
        if key == "isSidechain" and value in {"true", "false"}:
            value = value == "true"
        if key and value is not None:
            frame["values"][key] = value

    def _finish_value(self, frame: dict[str, Any]) -> None:
        frame["key"] = None
        frame["state"] = "comma"

    def _close_container(self, expected: str) -> None:
        frame = self._stack.pop()
        if frame["kind"] != expected:
            self.valid = False
            return
        if frame["context"] == "content_item" and frame["values"].get("type") == "text":
            text = frame["values"].get("text")
            if isinstance(text, str):
                self.texts.append(text)
        if not self._stack:
            self.values = frame["values"]
            return
        parent = self._stack[-1]
        if frame["context"] == "message":
            parent["values"]["message"] = frame["values"]
        self._finish_value(parent)


class NativeClaudeTranscriptReader:
    """Read only a verified task/session range beneath the mounted transcript root."""

    def __init__(self, root: Path | str | None = None) -> None:
        configured = root or os.environ.get(
            "PROGENA_CLAUDE_TRANSCRIPT_ROOT", "/var/lib/progena/claude/projects"
        )
        self._root = Path(configured)

    def start_new_turn(self, session_id: str | None, cwd: str | None) -> dict[str, Any] | None:
        """Anchor a newly identified private SDK transcript from its first byte."""
        session = _valid_session_id(session_id)
        if session is None or _project_directory(cwd) is None:
            return None
        return {
            "provider": "claude_native",
            "session_id": session,
            "line_start": 0,
            "byte_start": 0,
        }

    def start_turn(self, session_id: str | None, cwd: str | None) -> dict[str, Any] | None:
        """Capture an append-only native source boundary before a prompt starts."""
        session = _valid_session_id(session_id)
        project = _project_directory(cwd)
        if session is None or project is None:
            return None
        path = self._verified_path(session, project, cwd) or self._path(
            session, project, require_file=False
        )
        if path is None:
            return None
        if path.exists():
            try:
                byte_start, line_start, ends_on_newline = self._boundary(path)
            except _TranscriptReadError:
                return None
        else:
            byte_start, line_start, ends_on_newline = 0, 0, True
        # A pre-existing partial row cannot be assigned safely to either turn.
        if byte_start and not ends_on_newline:
            return None
        return {
            "provider": "claude_native",
            "session_id": session,
            "line_start": line_start,
            "byte_start": byte_start,
        }

    def finish_turn(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> dict[str, Any] | None:
        """Seal and hash the exact native byte range produced by this turn."""
        if not isinstance(reference, dict) or reference.get("provider") != "claude_native":
            return None
        session = _valid_session_id(reference.get("session_id"))
        project = _project_directory(cwd)
        start = reference.get("line_start")
        byte_start = reference.get("byte_start")
        if (
            session is None
            or project is None
            or not isinstance(start, int)
            or start < 0
            or not isinstance(byte_start, int)
            or byte_start < 0
        ):
            return None
        path = self._verified_path(session, project, cwd)
        if path is None:
            return None
        try:
            byte_end, rows, _ends_on_newline = self._boundary_from(path, byte_start)
            block_hashes = self._block_hashes(path, byte_start, byte_end)
        except _TranscriptReadError:
            return None
        return {
            **reference,
            "cwd": cwd,
            "line_start": start,
            "line_end": start + rows,
            "byte_start": byte_start,
            "byte_end": byte_end,
            "block_size": _VERIFY_BLOCK_BYTES,
            "block_sha256": block_hashes,
        }

    def final_output(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> tuple[str | None, int | None]:
        """Return the final complete main-assistant message, never an earlier fallback."""
        resolved = self._resolve_reference(reference, cwd=cwd)
        if resolved is None:
            return None, None
        final_id: str | None = None
        final_line: int | None = None
        final_texts: list[str] = []
        incomplete = False

        def select(line: int, row: _JSONLRow) -> None:
            nonlocal final_id, final_line, final_texts, incomplete
            if not row.complete():
                incomplete = True
                return
            values = row.values
            message = values.get("message")
            if (
                values.get("type") != "assistant"
                or values.get("isSidechain") is True
                or not isinstance(message, dict)
                or message.get("role") != "assistant"
            ):
                return
            message_id = message.get("id")
            if not isinstance(message_id, str):
                final_id = None
                final_texts = list(row.texts)
            elif message_id == final_id:
                final_texts.extend(row.texts)
            else:
                final_id = message_id
                final_texts = list(row.texts)
            final_line = line

        try:
            self._verify_blocks(resolved, resolved.byte_start, resolved.byte_end)
            self._scan_rows(resolved, select, collect_text=True)
        except _TranscriptReadError:
            return None, None
        if incomplete or final_line is None or not final_texts:
            return None, None
        return "".join(final_texts), final_line

    def continue_turn(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> dict[str, Any] | None:
        """Continue a prior sealed range without rescanning historical bytes."""
        resolved = self._resolve_reference(reference, cwd=cwd)
        if resolved is None:
            return None
        session = reference.get("session_id") if isinstance(reference, dict) else None
        if not isinstance(session, str):
            return None
        return {
            "provider": "claude_native",
            "session_id": session,
            "line_start": resolved.line_end,
            "byte_start": resolved.byte_end,
            "awaiting_identity": True,
        }

    def transcript_page(
        self,
        reference: dict[str, Any] | None,
        *,
        cwd: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any] | None:
        """Return one exact page without reading outside the sealed byte range."""
        resolved = self._resolve_reference(reference, cwd=cwd)
        if resolved is None:
            return None
        try:
            byte_cursor = self._page_byte_cursor(cursor, resolved)
            scan_end = min(resolved.byte_end, byte_cursor + limit * 4)
            self._verify_blocks(resolved, byte_cursor, scan_end)
            pieces: list[str] = []
            remaining = limit
            consumed = 0
            for _line, _column, fragment in _text_fragments(
                resolved.path,
                start_byte=byte_cursor,
                end_byte=scan_end,
                final=scan_end == resolved.byte_end,
            ):
                if len(fragment) > remaining:
                    visible = fragment[:remaining]
                    pieces.append(visible)
                    consumed += len(visible.encode("utf-8"))
                    remaining = 0
                    break
                pieces.append(fragment)
                consumed += len(fragment.encode("utf-8"))
                remaining -= len(fragment)
                if remaining == 0:
                    break
        except _TranscriptReadError:
            return None
        next_byte = byte_cursor + consumed
        has_next = next_byte < resolved.byte_end
        return {
            "content": "".join(pieces),
            "next_cursor": f"transcriptb:{next_byte}" if has_next else None,
            "truncated": has_next,
            "line_start": resolved.line_start,
            "line_end": resolved.line_end,
        }

    def _page_byte_cursor(self, cursor: str | None, resolved: _ResolvedTranscript) -> int:
        if cursor is None:
            return resolved.byte_start
        prefix, separator, value = cursor.partition(":")
        if prefix == "transcriptb" and separator and value.isdecimal():
            byte_cursor = int(value)
            if resolved.byte_start <= byte_cursor <= resolved.byte_end:
                return byte_cursor
            raise _TranscriptReadError("transcript cursor is outside the sealed turn")
        raise ACPResultRequestError("Invalid cursor for ACP transcript result")

    def _resolve_reference(
        self, reference: dict[str, Any] | None, *, cwd: str | None
    ) -> _ResolvedTranscript | None:
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
            or (isinstance(reference.get("cwd"), str) and reference["cwd"] != cwd)
        ):
            return None
        byte_start = reference.get("byte_start")
        byte_end = reference.get("byte_end")
        block_size = reference.get("block_size")
        block_hashes = reference.get("block_sha256")
        if (
            not isinstance(byte_start, int)
            or not isinstance(byte_end, int)
            or byte_start < 0
            or byte_end < byte_start
            or block_size != _VERIFY_BLOCK_BYTES
            or not isinstance(block_hashes, list)
            or any(not isinstance(value, str) for value in block_hashes)
            or len(block_hashes)
            != (byte_end - byte_start + _VERIFY_BLOCK_BYTES - 1) // _VERIFY_BLOCK_BYTES
        ):
            return None
        path = self._path(session, project, require_file=True)
        if path is None:
            return None
        try:
            if path.stat().st_size < byte_end:
                return None
        except OSError:
            return None
        return _ResolvedTranscript(path, start, end, byte_start, byte_end, tuple(block_hashes))

    def _path(self, session: str, project: str, *, require_file: bool) -> Path | None:
        candidates = self._paths(session, project, require_file=require_file)
        return next(candidates, None)

    def _paths(self, session: str, project: str, *, require_file: bool) -> Iterator[Path]:
        try:
            root = self._root.resolve(strict=True)
        except OSError:
            return
        if len(project) <= _MAX_PROJECT_PREFIX:
            project_paths = [root / project]
        elif require_file:
            prefix = f"{project[:_MAX_PROJECT_PREFIX]}-"
            try:
                project_paths = [
                    entry
                    for entry in root.iterdir()
                    if entry.name.startswith(prefix) and not entry.is_symlink()
                ]
            except OSError:
                return
        else:
            project_paths = [root / project]
        for project_path in project_paths:
            candidate = project_path / f"{session}.jsonl"
            try:
                if project_path.is_symlink() or (require_file and candidate.is_symlink()):
                    continue
                resolved = candidate.resolve(strict=require_file)
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
            if not require_file or resolved.is_file():
                yield resolved

    def _verified_path(self, session: str, project: str, cwd: str | None) -> Path | None:
        for path in self._paths(session, project, require_file=True):
            if self._metadata_matches(path, session, cwd):
                return path
        return None

    @staticmethod
    def _boundary(path: Path) -> tuple[int, int, bool]:
        return NativeClaudeTranscriptReader._boundary_from(path, 0)

    @staticmethod
    def _boundary_from(path: Path, start_byte: int) -> tuple[int, int, bool]:
        rows = 0
        nonempty = False
        last = b""
        total = start_byte
        try:
            with path.open("rb") as handle:
                handle.seek(start_byte)
                while chunk := handle.read(_STREAM_CHUNK_BYTES):
                    total += len(chunk)
                    rows += chunk.count(b"\n")
                    nonempty = True
                    last = chunk[-1:]
        except OSError as exc:
            raise _TranscriptReadError("transcript source is unavailable") from exc
        return total, rows + int(nonempty and last != b"\n"), not nonempty or last == b"\n"

    @staticmethod
    def _block_hashes(path: Path, start_byte: int, end_byte: int) -> list[str]:
        return [
            NativeClaudeTranscriptReader._hash_bytes(
                path,
                start_byte + offset,
                min(start_byte + offset + _VERIFY_BLOCK_BYTES, end_byte),
            )
            for offset in range(0, end_byte - start_byte, _VERIFY_BLOCK_BYTES)
        ]

    @staticmethod
    def _hash_bytes(path: Path, start_byte: int, end_byte: int) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                handle.seek(start_byte)
                remaining = end_byte - start_byte
                while remaining:
                    chunk = handle.read(min(_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise _TranscriptReadError("sealed transcript range is missing")
                    digest.update(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            raise _TranscriptReadError("transcript source is unavailable") from exc
        return digest.hexdigest()

    def _verify_blocks(self, resolved: _ResolvedTranscript, start_byte: int, end_byte: int) -> None:
        if not resolved.byte_start <= start_byte <= end_byte <= resolved.byte_end:
            raise _TranscriptReadError("invalid sealed transcript range")
        if start_byte == end_byte:
            return
        first = (start_byte - resolved.byte_start) // _VERIFY_BLOCK_BYTES
        last = (end_byte - 1 - resolved.byte_start) // _VERIFY_BLOCK_BYTES
        for index in range(first, last + 1):
            block_start = resolved.byte_start + index * _VERIFY_BLOCK_BYTES
            block_end = min(block_start + _VERIFY_BLOCK_BYTES, resolved.byte_end)
            actual = self._hash_bytes(resolved.path, block_start, block_end)
            if actual != resolved.block_hashes[index]:
                raise _TranscriptReadError("sealed transcript source changed")

    @staticmethod
    def _metadata_matches(path: Path, session: str, cwd: str | None) -> bool:
        if not isinstance(cwd, str):
            return False
        matched = False

        def inspect(_line: int, row: _JSONLRow) -> bool:
            nonlocal matched
            if (
                row.complete()
                and row.values.get("sessionId") == session
                and row.values.get("cwd") == cwd
            ):
                matched = True
            return matched

        try:
            NativeClaudeTranscriptReader._scan_rows(
                _ResolvedTranscript(path, 0, 512, 0, path.stat().st_size, ()), inspect
            )
        except (OSError, _TranscriptReadError):
            return False
        return matched

    @staticmethod
    def _scan_rows(
        resolved: _ResolvedTranscript,
        callback: Callable[[int, _JSONLRow], bool | None],
        *,
        collect_text: bool = False,
    ) -> None:
        current_line = -1
        row: _JSONLRow | None = None
        for line, _column, fragment in _text_fragments(
            resolved.path,
            start_byte=resolved.byte_start,
            end_byte=resolved.byte_end,
            line=resolved.line_start,
        ):
            if line >= resolved.line_end:
                break
            if line != current_line:
                current_line = line
                row = _JSONLRow(collect_text=collect_text)
            if row is None:
                continue
            row.feed(fragment)
            if fragment == "\n":
                if callback(line, row) is True:
                    return
                row = None
                current_line = -1
        if row is not None:
            callback(current_line, row)


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
