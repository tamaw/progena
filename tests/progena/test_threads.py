"""Boundary coverage for persisted checkpoint thread identities."""

import pytest

from src.progena.threads import (
    object_agent_thread_id,
    parse_routine_thread_id,
    routine_agent_thread_id,
    routine_session_from_thread,
    routine_thread_id,
)


def test_routine_thread_round_trip_preserves_pinned_and_unpinned_forms():
    pinned = routine_thread_id("routine-a", "revision-1", "session-a")
    unpinned = routine_thread_id("routine-a", None, "session-b")

    assert pinned == "routine:routine-a:revision-1:session-a"
    assert parse_routine_thread_id(pinned) == ("routine-a", "revision-1", "session-a")
    assert unpinned == "routine:routine-a:unpinned:session-b"
    assert parse_routine_thread_id(unpinned) == ("routine-a", None, "session-b")


@pytest.mark.parametrize(
    "thread_id",
    [
        "routine:routine-a:revision",
        "routine:routine-a::session-a",
        "routine::revision:session-a",
        "routine:routine-a:revision:session-a:extra",
        "other:routine-a:revision:session-a",
    ],
)
def test_routine_thread_parser_rejects_malformed_ids(thread_id):
    with pytest.raises(ValueError):
        parse_routine_thread_id(thread_id)


def test_routine_thread_parser_rejects_foreign_owner():
    thread_id = routine_thread_id("routine-a", "revision-1", "session-a")

    with pytest.raises(ValueError, match="does not belong"):
        routine_session_from_thread(thread_id, "routine-b")


def test_object_and_nested_thread_identities_preserve_serialized_forms():
    object_thread = object_agent_thread_id("worker", "caller:session")
    nested_thread = routine_agent_thread_id("routine:parent:revision:session", "delegate:retry")

    assert object_thread == "object-agent:worker:caller:session"
    assert nested_thread == "routine-agent:routine:parent:revision:session:delegate:retry"
