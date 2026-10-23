"""Runtime ``ReviewEvidenceSource`` backed by canonical stores and a live PR lookup.

Every read is by the review candidate's own identifiers and never writes: cards
through ``TaskStore.peek_kanban_card``, reviewer tasks through
``ACPTaskStore.get_acp_task`` (also non-creating), and pull requests through
the read-only GitHub lookup. Routine state, repo-manager output and the
heartbeat ``repo_scan`` are never consulted.
"""

from __future__ import annotations

from typing import Any

from src.progena.acp.store import ACPTaskStore
from src.progena.repositories.pull_requests import JsonGetter, fetch_pull_request_evidence
from src.progena.tasks.store import TaskStore


class StoredReviewEvidenceSource:
    """Trusted reads for ``review_provenance`` nodes."""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        acp_tasks: ACPTaskStore,
        get_json: JsonGetter | None = None,
    ) -> None:
        self._tasks = tasks
        self._acp_tasks = acp_tasks
        self._get_json = get_json

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        return await self._tasks.peek_kanban_card(card_id)

    async def get_acp_task(self, task_id: str) -> dict[str, Any] | None:
        record = await self._acp_tasks.get_acp_task(task_id)
        return record.model_dump(mode="json") if record is not None else None

    async def get_pull_request_evidence(self, pr_url: str) -> dict[str, Any] | None:
        if self._get_json is None:
            return await fetch_pull_request_evidence(pr_url)
        return await fetch_pull_request_evidence(pr_url, get_json=self._get_json)


def review_evidence_source_for(state_manager: Any, graph: Any) -> StoredReviewEvidenceSource | None:
    """Build the runtime source for a graph that has ``review_provenance`` nodes."""
    if not any(node.kind == "review_provenance" for node in graph.nodes):
        return None
    return StoredReviewEvidenceSource(tasks=state_manager.tasks, acp_tasks=state_manager.acp)
