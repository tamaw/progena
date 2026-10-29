"""Short, deterministic descriptions of the data a run step produced.

Run transcripts show speech in full but summarise data: what changed, a
document's mount path and title, a count, or both ends of a long text with a
pointer to where the whole value can be read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

# Chat's compact values are 96 characters (64 per field of a structure); a
# standalone text keeps both ends of up to ~150 characters before eliding.
INLINE_CHARS = 96
EDGE_CHARS = 70
_SHAPE_VALUE_CHARS = 64
_SHAPE_FIELDS = 6
_SMALL_JSON_CHARS = 200
_CHANGED_FIELDS = 3
_WHITESPACE = re.compile(r"\s+")


def one_line(text: str) -> str:
    """Collapse whitespace so a value fits on one transcript line."""
    return _WHITESPACE.sub(" ", text).strip()


def size_label(text: str) -> str:
    """Human size of ``text`` in UTF-8 bytes."""
    size = len(text.encode("utf-8"))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def head_tail(text: str, *, pointer: str | None = None, edge: int = EDGE_CHARS) -> str:
    """Both ends of a long text, its size and where the whole text can be read.

    Agent-written text tends to open with what it is doing and end with what it
    concluded, so keeping both ends beats keeping only the start.
    """
    flat = one_line(text)
    if len(flat) <= 2 * edge + 10:
        return f'"{flat}"'
    where = f"{size_label(text)} · {pointer}" if pointer else size_label(text)
    return f'"{flat[:edge].rstrip()}…" ⟨{where}⟩ "…{flat[-edge:].lstrip()}"'


def document_pointer(key: str, document: Any = None) -> str:
    """Where an agent can read a stored document: a mount path, or a typed id."""
    prefix, _, ident = key.partition(":")
    if not ident:
        return key
    if prefix == "kanban_card":
        project = document.get("project_id") if isinstance(document, Mapping) else None
        return f"/tasks/{project}/{ident}.md" if project else f"/tasks/{ident}.md"
    if prefix == "inbox_message":
        return f"/inbox/{ident}.md"
    if prefix == "alert":
        return f"/alerts/{ident}.json"
    if prefix == "workflow":
        return f"/workflows/{ident}.json"
    if prefix == "routine":
        return f"/routines/{ident}.json"
    if prefix in {"acp_task", "a2a_task"}:
        return f"{prefix} {ident[:8]}"
    return key


def _title(document: Any) -> str | None:
    if not isinstance(document, Mapping):
        return None
    for field in ("title", "name", "subject"):
        value = document.get(field)
        if isinstance(value, str) and value.strip():
            return one_line(value)
    return None


def _scalar(value: Any, *, limit: int, pointer: str | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = one_line(str(value))
    if not text:
        return '""'
    if len(text) <= limit:
        return text
    return head_tail(str(value), pointer=pointer)


def _short(value: Any) -> str:
    """One field inside a structure's shape."""
    if isinstance(value, Mapping):
        return f"{{{len(value)} fields}}"
    if isinstance(value, (list, tuple)):
        return f"{len(value)} items"
    if isinstance(value, str) and len(one_line(value)) > _SHAPE_VALUE_CHARS:
        return f"{size_label(value)} text"
    return _scalar(value, limit=_SHAPE_VALUE_CHARS, pointer=None)


def describe_change(event: Mapping[str, Any]) -> str:
    """``card_f34cf82c "title" status idle → validate`` from a document event."""
    key = str(event.get("key") or event.get("id") or "document")
    raw_after, raw_before = event.get("after"), event.get("before")
    after: Mapping[str, Any] = raw_after if isinstance(raw_after, Mapping) else {}
    before: Mapping[str, Any] = raw_before if isinstance(raw_before, Mapping) else {}
    changed = event.get("changed_fields")
    if isinstance(changed, Mapping):
        changed = list(changed)
    fields = [str(field) for field in changed] if isinstance(changed, list) else []
    fields = [field for field in fields if field not in {"updated_at", "timestamp"}]
    title = _title(after) or _title(before)
    head = document_pointer(key, after or before) + (f' "{title}"' if title else "")
    op = str(event.get("op") or "")
    if not fields:
        return f"{head} {op}".strip()
    parts = [
        f"{field} {_short(before.get(field))} → {_short(after.get(field))}"
        for field in fields[:_CHANGED_FIELDS]
    ]
    more = f" +{len(fields) - _CHANGED_FIELDS} more" if len(fields) > _CHANGED_FIELDS else ""
    return f"{head} " + ", ".join(parts) + more


def _visible(value: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): item for key, item in value.items() if not str(key).startswith("_")}


def describe_value(value: Any, *, pointer: str | None = None) -> str:
    """One line describing a step's output; long text keeps both ends plus ``pointer``."""
    if isinstance(value, (list, tuple)):
        return f"{len(value)} items"
    if not isinstance(value, Mapping):
        return _scalar(value, limit=INLINE_CHARS, pointer=pointer)
    if {"changed_fields", "after"} <= set(value) or {"before", "after", "key"} <= set(value):
        return describe_change(value)
    if "key" in value and ("document" in value or "found" in value):
        key = str(value["key"])
        if value.get("found") is False:
            return f"{document_pointer(key)} not found"
        document = value.get("document")
        title = _title(document)
        return document_pointer(key, document) + (f' "{title}"' if title else "")
    if "items" in value and ("count" in value or "total" in value):
        return f"{value.get('count', value.get('total'))} items"
    fields = _visible(value)
    if len(fields) == 1:
        ((only_key, only_value),) = fields.items()
        if only_key in {"text", "value", "message", "result"}:
            return describe_value(only_value, pointer=pointer)
        if only_value is True:  # a branch port: {"<port>": true}
            return only_key
    encoded = json.dumps(fields, ensure_ascii=False, default=str)
    if len(encoded) <= _SMALL_JSON_CHARS:
        return ", ".join(f"{key}: {_short(item)}" for key, item in fields.items())
    shown = list(fields.items())[:_SHAPE_FIELDS]
    more = len(fields) - len(shown)
    body = ", ".join(f"{key}: {_short(item)}" for key, item in shown)
    suffix = f", +{more} more" if more else ""
    where = f" ⟨{pointer}⟩" if pointer else ""
    return f"{{{body}{suffix}}}{where}"


class _BlockDumper(yaml.SafeDumper):
    """YAML whose long or multi-line strings are literal blocks, readable as written."""


def _represent_str(dumper: yaml.SafeDumper, text: str) -> yaml.ScalarNode:
    style = "|" if "\n" in text or len(text) > 80 else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", text, style=style)


_BlockDumper.add_representer(str, _represent_str)


def full_text(value: Any) -> str:
    """A step's whole value as readable text (YAML for structures, raw for text)."""
    if isinstance(value, str):
        return value
    return yaml.dump(value, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=1000)
