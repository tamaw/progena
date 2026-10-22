"""Storage access."""

from __future__ import annotations

import logging

from fastapi import HTTPException

import app.backend.monitoring.state as monitoring_state

logger = logging.getLogger(__name__)


def _require_document_store(state_reader: monitoring_state.StateReader):
    """Return the backend document store or fail loudly.

    Tasks are database-only: the board, task bodies, and activity all live in the
    shared Postgres ``store`` (the same documents the runtime agent reads/edits).
    There is no on-disk fallback, so a missing store is a hard 503.
    """
    if state_reader.document_store is None:
        raise HTTPException(status_code=503, detail="Document store is unavailable")
    return state_reader.document_store
