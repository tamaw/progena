"""Thread-id ContextVar for per-request tool state resolution."""

from __future__ import annotations

import contextvars
from typing import Any

_current_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_thread_id", default=None
)


def get_current_thread_id() -> str | None:
    """Return the thread id for the current async context, if any."""
    return _current_thread_id.get()


def set_current_thread_id(thread_id: str | None) -> contextvars.Token[Any]:
    """Set the thread id for the current async context.  Returns a token
    that must be passed to :pyfunc:`reset_current_thread_id` in a
    ``finally`` block.
    """
    return _current_thread_id.set(thread_id)


def reset_current_thread_id(token: contextvars.Token[Any]) -> None:
    """Reset the thread id ContextVar using the token returned by
    :pyfunc:`set_current_thread_id`.
    """
    _current_thread_id.reset(token)
