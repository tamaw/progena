"""Backend access to the ``preferences`` state document.

Preferences (display name, timezone) live in the ``preferences`` state document
(the single source of truth, also editable by the agent as
``/config/preferences.json``) in the shared ``progenitor.state`` namespace —
the same document ``app.progenitor.state.manager.StateManager`` reads/writes on
the runtime side. This module loads/saves that document from the backend
process; validation is shared via ``app.progenitor.state.preferences`` so the
two write paths can never disagree on shape.
"""

from __future__ import annotations

from typing import Any

from app.progenitor.state.preferences import PREFERENCES_DOC_KEY, default_preferences_payload

from .document_store import PROGENITOR_STATE_PREFIX, get_default_document_store


def load_preferences() -> dict[str, Any]:
    """Return the user preferences document from the document store."""
    default = default_preferences_payload()
    document = get_default_document_store().get_document(
        PROGENITOR_STATE_PREFIX, PREFERENCES_DOC_KEY, default
    )
    payload = document.payload if document is not None else default
    return payload if isinstance(payload, dict) else default


def save_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a validated preferences payload and return it."""
    get_default_document_store().save_document(
        PROGENITOR_STATE_PREFIX, PREFERENCES_DOC_KEY, payload
    )
    return payload
