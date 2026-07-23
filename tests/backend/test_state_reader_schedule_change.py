"""Schedule change detection in StateReader (the NOTIFY -> SSE live-update path).

A write to the ``schedule`` document fires the Postgres ``store_changed`` NOTIFY.
The backend StoreChangeListener turns that into ``_on_store_change``, which
recomputes the snapshot and pushes to SSE watchers. The entry count catches
add/remove; these tests additionally cover in-place edits (pause/resume) that
leave the count unchanged.
"""

from __future__ import annotations

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.state_reader import StateReader


def _reader(entries: dict[str, dict]) -> tuple[StateReader, InMemoryDocumentStore]:
    store = InMemoryDocumentStore()
    store.save_document(PROGENITOR_STATE_PREFIX, "schedule", entries)
    reader = StateReader(
        document_store=store,
    )
    return reader, store


def _resnapshot(reader: StateReader) -> object:
    return reader.get_snapshot()


def test_store_change_threads_schedule_key_to_watchers():
    reader, _store = _reader(
        {"sched_a": {"id": "sched_a", "message": "hi", "cron_expression": "0 9 * * *"}}
    )
    seen: list[tuple[object, object]] = []
    reader.add_watcher(lambda snapshot, key: seen.append((snapshot, key)))

    # NOTIFY payload carries the changed store key; it must reach the watcher.
    reader._on_store_change('{"prefix": "progenitor.state", "key": "schedule"}')

    assert len(seen) == 1
    assert seen[0][1] == "schedule"


def test_adding_a_schedule_entry_is_detected():
    reader, store = _reader(
        {"sched_a": {"id": "sched_a", "message": "hi", "cron_expression": "0 9 * * *"}}
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "schedule",
        {
            "sched_a": {"id": "sched_a", "message": "hi", "cron_expression": "0 9 * * *"},
            "sched_b": {"id": "sched_b", "message": "bye", "cron_expression": "0 10 * * *"},
        },
    )
    snap2 = _resnapshot(reader)

    assert len(snap2.schedule) != len(snap1.schedule)
    assert reader._state_changed(snap2)


def test_in_place_pause_is_detected_via_fingerprint():
    # Same entry count, only a field flips - must still push live.
    reader, store = _reader(
        {
            "sched_a": {
                "id": "sched_a",
                "message": "hi",
                "cron_expression": "0 9 * * *",
                "paused": False,
            }
        },
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "schedule",
        {
            "sched_a": {
                "id": "sched_a",
                "message": "hi",
                "cron_expression": "0 9 * * *",
                "paused": True,
            }
        },
    )
    snap2 = _resnapshot(reader)

    assert len(snap2.schedule) == len(snap1.schedule)
    assert snap2.schedule_fingerprint != snap1.schedule_fingerprint
    assert reader._state_changed(snap2)


def test_unchanged_schedule_is_not_flagged():
    reader, _store = _reader(
        {"sched_a": {"id": "sched_a", "message": "hi", "cron_expression": "0 9 * * *"}}
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    snap2 = _resnapshot(reader)

    assert snap2.schedule_fingerprint == snap1.schedule_fingerprint
    assert not reader._state_changed(snap2)
