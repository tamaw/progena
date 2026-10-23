"""Connection-scoped document-event interests for UI canvases."""

from __future__ import annotations

from collections.abc import Iterable

import src.backend.events.stream as events_stream
from src.backend.events.documents import TypedDocumentEvent


class CanvasEvents:
    """Track each canvas frame's document-event subscription patterns."""

    def __init__(self) -> None:
        self.interests: dict[tuple[str, str], frozenset[str]] = {}

    def set_interest(self, client_id: str, name: str, patterns: Iterable[str]) -> None:
        self.interests[(client_id, name)] = frozenset(patterns)

    def clear_interest(self, client_id: str, name: str) -> None:
        self.interests.pop((client_id, name), None)

    def disconnected(self, client_id: str) -> None:
        for key in [key for key in self.interests if key[0] == client_id]:
            self.interests.pop(key, None)

    async def document_event(self, typed: TypedDocumentEvent) -> None:
        """Deliver a typed document event to every matching canvas frame."""

        if typed.output is None or typed.document_type is None:
            return
        event = typed.event
        document_type = typed.document_type.id
        matching = (f"{document_type}.{event.op}", f"{document_type}.*", f"*.{event.op}", "*.*")
        payload = {
            "event_id": typed.output["event_id"],
            "document_type": typed.output["document_type"],
            "key": typed.output["key"],
            "id": typed.output["id"],
            "op": typed.output["op"],
            "timestamp": typed.output["timestamp"],
            "changed_fields": typed.output["changed_fields"],
            "document": event.after if event.op != "deleted" else None,
        }

        for (client_id, name), patterns in list(self.interests.items()):
            if patterns.intersection(matching):
                events_stream.broadcast_ui_event(
                    "canvas.document.event",
                    {"name": name, "event": payload},
                    client_id=client_id,
                )


canvas_events = CanvasEvents()
