"""Agents-page A2A task reads use compact per-task documents."""

from __future__ import annotations

from src.backend.monitoring.state import StateReader
from src.backend.storage.documents import PROGENA_STATE_PREFIX, InMemoryDocumentStore


def test_agents_reader_uses_per_task_documents_not_retired_singleton():
    store = InMemoryDocumentStore()
    store.save_document(
        PROGENA_STATE_PREFIX,
        "a2a_tasks",
        [{"task_id": "legacy", "agent_name": "legacy-agent"}],
    )
    store.save_document(
        PROGENA_STATE_PREFIX,
        "a2a_task:current",
        {
            "task_id": "current",
            "agent_name": "current-agent",
            "status": "working",
            "summary": "compact",
            "latest_task": None,
            "evidence_refs": {"latest_task": {"key": "sha256:abc"}},
        },
    )

    tasks = StateReader(document_store=store).get_a2a_tasks()

    assert tasks == [
        {
            "task_id": "current",
            "agent_name": "current-agent",
            "status": "working",
            "summary": "compact",
            "latest_task": None,
            "evidence_refs": {"latest_task": {"key": "sha256:abc"}},
        }
    ]
