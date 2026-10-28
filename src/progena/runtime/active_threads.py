"""Conversation threads this runtime process is running an agent turn on.

Direct chat turns are tracked by ``RuntimeChatService``'s per-thread lock;
this registry covers turns the heartbeat runs on a conversation thread
(scheduled messages, completion follow-ups), so a state read never reports
such a thread as interrupted while its turn is still executing.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager

_running: Counter[str] = Counter()


@contextmanager
def running_turn(thread_id: str) -> Iterator[None]:
    """Mark ``thread_id`` as executing for the duration of the block."""
    _running[thread_id] += 1
    try:
        yield
    finally:
        _running[thread_id] -= 1
        if _running[thread_id] <= 0:
            del _running[thread_id]


def is_running(thread_id: str) -> bool:
    """Whether this process is executing a heartbeat turn on ``thread_id``."""
    return _running.get(thread_id, 0) > 0
