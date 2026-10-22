"""Workflows document persistence."""

from __future__ import annotations

from typing import Any

from src.progenitor.documents.kinds import (
    WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from src.progenitor.persistence.documents import StateDocuments
from src.progenitor.utils import utcnow_isoformat
from src.progenitor.workflows.documents import workflow_definition_key, workflow_invoke_request_key


class WorkflowStore:
    """Own workflows document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_workflow_definition(self, workflow_id: str) -> dict[str, Any] | None:
        """Return one workflow definition document, or ``None`` if missing."""
        key = workflow_definition_key(workflow_id)
        payload = await self._access.store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_workflow_definition(self, workflow_id: str, payload: dict[str, Any]) -> None:
        """Persist one workflow definition document."""
        key = workflow_definition_key(workflow_id)
        await self._access.save(key, payload)

    async def delete_workflow_definition(self, workflow_id: str) -> None:
        """Remove one workflow definition document."""
        key = workflow_definition_key(workflow_id)
        await self._access.store.delete_document(key)

    async def list_workflow_definitions(self) -> list[dict[str, Any]]:
        """Return all workflow definition documents."""
        rows = await self._access.store.list_documents(WORKFLOW_DEFINITION_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    async def list_workflow_runs(self) -> list[dict[str, Any]]:
        """Return all workflow run documents."""
        rows = await self._access.store.list_documents(WORKFLOW_RUN_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]

    async def request_workflow_run(
        self,
        *,
        request_id: str,
        workflow_id: str,
        input: dict[str, Any],
        ancestry: list[str],
    ) -> None:
        """Persist a durable request for the backend workflow executor."""
        await self._access.save(
            workflow_invoke_request_key(request_id),
            {
                "request_id": request_id,
                "workflow_id": workflow_id,
                "input": input,
                "ancestry": ancestry,
                "requested_at": utcnow_isoformat(),
            },
        )

    async def request_workflow_approval(
        self, *, request_id: str, workflow_id: str, run_id: str, approved: bool
    ) -> None:
        """Persist Progenitor's answer to a workflow approval for the backend to apply."""
        await self._access.save(
            f"{WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX}{request_id}",
            {
                "request_id": request_id,
                "workflow_id": workflow_id,
                "run_id": run_id,
                "approved": approved,
                "requested_at": utcnow_isoformat(),
            },
        )
