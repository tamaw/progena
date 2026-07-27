"""Workflow change detection in StateReader (the NOTIFY -> SSE live-update path).

A write to a ``workflow:<id>`` document (e.g. the agent writing
/workflows/<id>.json) fires the Postgres ``store_changed`` NOTIFY. Like
kanban/inbox, no other snapshot field changes on a workflow-only write, so
``workflow_fingerprint`` is the sole signal ``_state_changed`` has for it.
"""

from __future__ import annotations

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.state_reader import StateReader


def _reader() -> tuple[StateReader, InMemoryDocumentStore]:
    store = InMemoryDocumentStore()
    reader = StateReader(
        document_store=store,
    )
    return reader, store


def _resnapshot(reader: StateReader) -> object:
    return reader.get_snapshot()


def test_store_change_threads_workflow_key_to_watchers():
    reader, _store = _reader()
    seen: list[tuple[object, object]] = []
    reader.add_watcher(lambda snapshot, key: seen.append((snapshot, key)))

    reader._on_store_change('{"prefix": "progenitor.state", "key": "workflow:wf_a"}')

    assert len(seen) == 1
    assert seen[0][1] == "workflow:wf_a"


def test_new_workflow_definition_is_detected_via_fingerprint():
    reader, store = _reader()
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow:wf_a",
        {"id": "wf_a", "title": "New workflow", "nodes": [], "edges": []},
    )
    snap2 = _resnapshot(reader)

    assert snap2.workflow_fingerprint != snap1.workflow_fingerprint
    assert reader._state_changed(snap2)


def test_workflow_run_update_is_detected_via_fingerprint():
    reader, store = _reader()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow_run:run_a",
        {"run_id": "run_a", "workflow_id": "wf_a", "status": "queued"},
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow_run:run_a",
        {"run_id": "run_a", "workflow_id": "wf_a", "status": "running"},
    )
    snap2 = _resnapshot(reader)

    assert snap2.workflow_fingerprint != snap1.workflow_fingerprint
    assert reader._state_changed(snap2)


def test_workflow_invoke_request_is_detected_for_reconciliation():
    reader, store = _reader()
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow_invoke_request:req_a",
        {
            "request_id": "req_a",
            "workflow_id": "wf_a",
            "requested_at": "2026-07-26T00:00:00+00:00",
        },
    )
    snap2 = _resnapshot(reader)

    assert snap2.workflow_fingerprint != snap1.workflow_fingerprint
    assert reader._state_changed(snap2)


def test_unchanged_workflows_are_not_flagged():
    reader, store = _reader()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow:wf_a",
        {"id": "wf_a", "title": "New workflow", "nodes": [], "edges": []},
    )
    snap1 = reader.get_snapshot()
    reader._last_snapshot = snap1

    snap2 = _resnapshot(reader)

    assert snap2.workflow_fingerprint == snap1.workflow_fingerprint
    assert not reader._state_changed(snap2)


def test_workflow_notify_reaches_watchers_even_when_snapshot_is_unchanged():
    reader, store = _reader()
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        "workflow:wf_a",
        {"id": "wf_a", "title": "Existing workflow", "nodes": [], "edges": []},
    )
    reader._last_snapshot = reader.get_snapshot()
    seen: list[str | None] = []
    reader.add_watcher(lambda _snapshot, key: seen.append(key))

    reader._on_store_change('{"prefix": "progenitor.state", "key": "workflow:wf_a"}')

    assert seen == ["workflow:wf_a"]
