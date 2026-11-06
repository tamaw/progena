"""Shared session status contracts."""

from __future__ import annotations

from typing import Literal

CANCELLED_STATUS = "cancelled"
SessionTurnStatus = Literal["completed", "failed", "cancelled"]


def terminal_session_fields(status: SessionTurnStatus) -> dict[str, bool]:
    """Return the document fields changed by one terminal conversation turn."""
    if status == "completed":
        return {"unread": True, "errored": False}
    if status == "failed":
        return {"unread": True, "errored": True}
    return {"unread": True}
