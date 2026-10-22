"""Documents capabilities."""

from __future__ import annotations

from typing import Any

import yaml

from src.progenitor.documents.markdown import _split_frontmatter
from src.progenitor.sessions.context import get_current_thread_id
from src.progenitor.utils import utcnow_isoformat


def inbox_message_to_markdown(message: dict[str, Any]) -> str:
    """Render one inbox message as YAML frontmatter (fields) + markdown body."""
    fields = {k: v for k, v in message.items() if k != "body"}
    body = str(message.get("body") or "").strip("\n")
    frontmatter = yaml.safe_dump(
        fields, sort_keys=True, default_flow_style=False, allow_unicode=True
    ).strip()
    if body:
        return f"---\n{frontmatter}\n---\n\n{body}\n"
    return f"---\n{frontmatter}\n---\n"


def markdown_to_inbox_message(content: str, message_id: str) -> dict[str, Any]:
    """Parse an ``inbox/<id>.md`` file into a message dict.

    The filename is authoritative for ``id`` (same convention as kanban cards).
    The markdown body becomes ``body``. A non-empty ``title`` is the only hard
    requirement; ``read`` is coerced to a bool and ``created_at`` is stamped
    when absent so agent-written files need only a title and a body. ``thread_id``
    is likewise stamped from the current request's thread context when absent —
    it is system-owned (never agent-authored) so an attachment referenced from
    that thread can later be resolved with the same scoping the attachments API
    already enforces. Both are backfilled only when missing, so an edit never
    overwrites the values a message was originally created with. Unknown
    frontmatter keys round-trip untouched.
    """
    fields, body = _split_frontmatter(content)
    message: dict[str, Any] = dict(fields)
    message["id"] = message_id
    body = body.strip("\n")
    message["body"] = body
    if not str(message.get("title") or "").strip():
        raise ValueError("message is missing a 'title'")
    message["read"] = bool(message.get("read", False))
    if not str(message.get("created_at") or "").strip():
        message["created_at"] = utcnow_isoformat()
    if not str(message.get("thread_id") or "").strip():
        thread_id = get_current_thread_id()
        if thread_id:
            message["thread_id"] = thread_id
    message.setdefault("read_at", None)
    return message
