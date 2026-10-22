"""Routines document persistence."""

from __future__ import annotations

from typing import Any

from src.progenitor.persistence.documents import StateDocuments
from src.progenitor.routines.store import ROUTINE_GRAPH_KEY_PREFIX, routine_graph_key


class RoutineStore:
    """Own routines document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def save_routine_run_event(self, payload: dict[str, Any]) -> None:
        """Persist lifecycle transitions without retaining them in the state cache."""
        from ..routines.events import RUN_EVENT_PREFIX

        key = f"{RUN_EVENT_PREFIX}{payload['request_id']}:{payload['status']}"
        await self._access.store.save_document(key, payload)

    async def get_routine_approval_decision(self, thread_id: str, interrupt_id: str) -> Any:
        from ..routines.approvals import decision_key

        return await self._access.store.peek_document(decision_key(thread_id, interrupt_id))

    async def get_routine_graph(self, agent_id: str) -> dict[str, Any] | None:
        """Return one agent's routine graph document, or ``None`` if missing."""
        key = routine_graph_key(agent_id)
        payload = await self._access.store.peek_document(key)
        return payload if isinstance(payload, dict) else None

    async def save_routine_graph(self, agent_id: str, payload: dict[str, Any]) -> None:
        """Persist one agent's routine graph document."""
        key = routine_graph_key(agent_id)
        await self._access.save(key, payload)

    async def delete_routine_graph(self, agent_id: str) -> None:
        """Remove one agent's routine graph document."""
        key = routine_graph_key(agent_id)
        await self._access.store.delete_document(key)

    async def list_routine_graphs(self) -> list[dict[str, Any]]:
        """Return all routine graph documents."""
        rows = await self._access.store.list_documents(ROUTINE_GRAPH_KEY_PREFIX)
        return [payload for _, payload in rows if isinstance(payload, dict)]
