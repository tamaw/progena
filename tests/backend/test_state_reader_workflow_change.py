"""Workflow change detection in StateReader (the NOTIFY -> SSE live-update path).

A write to a ``workflow:<id>`` document (e.g. the agent writing
/state/workflows/<id>.json) fires the Postgres ``store_changed`` NOTIFY. Like
kanban/inbox, no other snapshot field changes on a workflow-only write, so
``workflow_fingerprint`` is the sole signal ``_state_changed`` has for it.
"""

from __future__ import annotations

from pathlib import Path

from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.state_reader import StateReader


def _reader(tmp_path: Path) -> tuple[StateReader, InMemoryDocumentStore]:
    store = InMemoryDocumentStore()
    for name in ("state", "config", "sessions", "logs"):
        (tmp_path / name).mkdir()
    reader = StateReader(
        state_dir=tmp_path / "state",
        config_dir=tmp_path / "config",
        sessions_dir=tmp_path / "sessions",
        logs_dir=tmp_path / "logs",
        document_store=store,
    )
    return reader, store


def _resnapshot(reader: StateReader) -> object:
    reader._cache.clear()
    return reader.get_snapshot()


def test_store_change_threads_workflow_key_to_watchers(tmp_path):
    reader, _store = _reader(tmp_path)
    seen: list[tuple[object, object]] = []
    reader.add_watcher(lambda snapshot, key: seen.append((snapshot, key)))

    reader._on_store_change('{"prefix": "progenitor.state", "key": "workflow:wf_a"}')

    assert len(seen) == 1
    assert seen[0][1] == "workflow:wf_a"


def test_new_workflow_definition_is_detected_via_fingerprint(tmp_path):
    reader, store = _reader(tmp_path)
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


def test_workflow_run_update_is_detected_via_fingerprint(tmp_path):
    reader, store = _reader(tmp_path)
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


def test_unchanged_workflows_are_not_flagged(tmp_path):
    reader, store = _reader(tmp_path)
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
