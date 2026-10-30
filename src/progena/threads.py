"""Authoritative construction and validation for persisted checkpoint thread IDs."""

from __future__ import annotations

MAIN_THREAD_ID = "progena_main"
_ROUTINE_PREFIX = "routine"
_ROUTINE_AGENT_PREFIX = "routine-agent"
_OBJECT_AGENT_PREFIX = "object-agent"
_UNPINNED_REVISION = "unpinned"


def routine_default_session_id(routine_id: str) -> str:
    """Construct the provider session for a direct routine invocation."""
    return f"{_ROUTINE_PREFIX}:{routine_id}"


def routine_thread_id(routine_id: str, revision_id: str | None, session_id: str) -> str:
    """Construct a routine checkpoint thread ID in its persisted form."""
    return f"{_ROUTINE_PREFIX}:{routine_id}:{revision_id or _UNPINNED_REVISION}:{session_id}"


def parse_routine_thread_id(
    thread_id: str, agent_id: str | None = None
) -> tuple[str, str | None, str]:
    """Strictly parse a persisted routine thread and optionally enforce ownership."""
    parts = thread_id.split(":")
    if len(parts) != 4 or parts[0] != _ROUTINE_PREFIX or not all(parts[1:]):
        raise ValueError(f"not a routine run thread_id: {thread_id!r}")
    routine_id, revision_id, session_id = parts[1:]
    if agent_id is not None and routine_id != agent_id:
        raise ValueError("routine thread does not belong to the requested agent")
    return routine_id, (None if revision_id == _UNPINNED_REVISION else revision_id), session_id


def routine_session_from_thread(thread_id: str, agent_id: str | None = None) -> str:
    """Return the logical session from a validated, optionally owned routine thread."""
    return parse_routine_thread_id(thread_id, agent_id)[2]


def object_agent_session_id(assistant_id: str) -> str:
    """Construct the provider session for a non-checkpointed object-agent call."""
    return f"{_OBJECT_AGENT_PREFIX}:{assistant_id}"


def object_agent_thread_id(assistant_id: str, thread_id: str) -> str:
    """Construct the checkpoint thread used by one object-agent protocol thread."""
    return f"{_OBJECT_AGENT_PREFIX}:{assistant_id}:{thread_id}"


def routine_agent_thread_id(parent_thread_id: str, node_id: str) -> str:
    """Construct the checkpoint thread for an object agent delegated by a routine."""
    return f"{_ROUTINE_AGENT_PREFIX}:{parent_thread_id}:{node_id}"
