"""Tasks document persistence."""

from __future__ import annotations

from typing import Any

from src.progena.persistence.documents import StateDocuments


class TaskStore:
    """Own tasks document operations."""

    def __init__(self, access: StateDocuments) -> None:
        self._access = access

    async def get_kanban_structure(self) -> dict[str, Any]:
        """Return the kanban board structure document (columns + ``card_order``).

        Read straight from the shared store (not the cache): the backend (UI)
        edits this same document, so a cached copy would hide cross-process
        changes. See get_mcp_config for the same rationale.
        """
        default: dict[str, Any] = {
            "id": "progena-kanban",
            "columns": [],
            "card_order": [],
        }
        payload = await self._access.store.get_document("kanban_board", default)
        return payload if isinstance(payload, dict) else dict(default)

    async def save_kanban_structure(self, structure: dict[str, Any]) -> None:
        """Persist the kanban board structure document."""
        await self._access.save("kanban_board", structure)

    @staticmethod
    def _kanban_card_key(card_id: str) -> str:
        return f"kanban_card:{card_id}"

    async def list_kanban_card_ids(self) -> list[str]:
        """Return the ordered list of card ids from the structure document."""
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        if not isinstance(order, list):
            return []
        return [str(cid).strip() for cid in order if str(cid).strip()]

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        """Return one card document, or ``None`` if it does not exist."""
        key = self._kanban_card_key(card_id)
        payload = await self._access.store.get_document(key, None)
        return payload if isinstance(payload, dict) else None

    async def peek_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        """Return one card document, or ``None``, without ever writing.

        ``get_kanban_card`` materializes a default row for a missing key; use this
        wherever the id comes from outside (e.g. a routine's review candidate) so a
        forged or stale id cannot create a document.
        """
        payload = await self._access.store.peek_document(self._kanban_card_key(card_id))
        return payload if isinstance(payload, dict) else None

    async def save_kanban_card(self, card_id: str, card: dict[str, Any]) -> None:
        """Persist one card document and ensure it is tracked in ``card_order``."""
        key = self._kanban_card_key(card_id)
        await self._access.save(key, card)
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        order = (
            [str(cid).strip() for cid in order if str(cid).strip()]
            if isinstance(order, list)
            else []
        )
        if card_id not in order:
            order.append(card_id)
            structure["card_order"] = order
            await self.save_kanban_structure(structure)

    async def delete_kanban_card(self, card_id: str) -> None:
        """Remove one card document and drop it from ``card_order``."""
        key = self._kanban_card_key(card_id)
        await self._access.store.delete_document(key)
        structure = await self.get_kanban_structure()
        order = structure.get("card_order")
        if isinstance(order, list) and card_id in order:
            structure["card_order"] = [cid for cid in order if cid != card_id]
            await self.save_kanban_structure(structure)

    async def get_kanban_board(self) -> dict[str, Any]:
        """Assemble the full board (structure + per-card documents).

        Tolerant of a legacy structure that still carries an inline ``cards``
        array (the backend migrates those to per-card rows on its next read).
        """
        structure = await self.get_kanban_structure()
        inline = structure.get("cards")
        if isinstance(inline, list) and inline and "card_order" not in structure:
            cards = [card for card in inline if isinstance(card, dict)]
        else:
            cards = []
            for card_id in await self.list_kanban_card_ids():
                card = await self.get_kanban_card(card_id)
                if card is not None:
                    cards.append(card)
        assembled = {k: v for k, v in structure.items() if k not in ("cards", "card_order")}
        assembled["cards"] = cards
        return assembled
