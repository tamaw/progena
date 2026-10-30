"""Native compaction summary marker and body extraction contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUMMARY_MARKER_KEY = "progena_summary"
_NATIVE_NO_ARCHIVE_PREFIX = "Here is a summary of the conversation to date:\n\n"


def summary_marker(summary: str, archive_path: str | None) -> dict[str, str | None]:
    """Build the durable metadata record for a generated summary body."""
    return {"summary": summary, "archive_path": archive_path}


def summary_body(message: Any) -> str | None:
    """Return the actual native generated summary, never its retrieval wrapper."""
    if isinstance(message, Mapping):
        payload = message.get("data")
        message = payload if isinstance(payload, Mapping) else message
        additional = message.get("additional_kwargs")
        content = message.get("content")
    else:
        additional = getattr(message, "additional_kwargs", None)
        content = getattr(message, "content", None)
    if isinstance(additional, Mapping):
        marker = additional.get(SUMMARY_MARKER_KEY)
        if isinstance(marker, Mapping) and isinstance(marker.get("summary"), str):
            return marker["summary"] or None
    if not isinstance(content, str) or not content:
        return None
    opening = content.find("<summary>")
    closing = content.rfind("</summary>")
    if opening >= 0 and closing > opening:
        body_start = opening + len("<summary>")
        if content.startswith("\n", body_start):
            body_start += 1
        body_end = closing - 1 if content[closing - 1] == "\n" else closing
        return content[body_start:body_end] or None
    if content.startswith(_NATIVE_NO_ARCHIVE_PREFIX):
        return content.removeprefix(_NATIVE_NO_ARCHIVE_PREFIX) or None
    return None
