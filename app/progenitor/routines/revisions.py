"""Async readers for immutable routine graph revisions.

The backend (``app/backend/document_store.py``) owns the DDL for the
``routine_graph_revisions`` table and the trigger that populates it. This
module only reads: it opens a short-lived ``psycopg.AsyncConnection`` per
fetch and closes it in ``finally``, because fetches are rare in steady state
(the routine subagent proxy in :mod:`app.progenitor.routines.subagent` caches
compiled graphs by ``revision_id``, so a pinned invocation issues no SQL at
all once cached).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, cast

import psycopg
from psycopg.rows import DictRow, dict_row

logger = logging.getLogger(__name__)

_missing_table_logged = False


class RoutineRevisionReader(Protocol):
    """Resolves routine graph revisions for the runtime's subagent proxy."""

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None: ...

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None: ...


class PostgresRoutineRevisionReader:
    """Reads revisions from the backend-owned ``routine_graph_revisions`` table."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None:
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self._database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT revision_id, graph FROM routine_graph_revisions "
                    "WHERE routine_id = %s ORDER BY captured_at DESC, revision_id DESC LIMIT 1",
                    (routine_id,),
                )
                row = await cur.fetchone()
        except psycopg.errors.UndefinedTable:
            _log_missing_table_once()
            return None
        finally:
            await conn.close()
        if row is None:
            return None
        return row["revision_id"], row["graph"]

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self._database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT graph FROM routine_graph_revisions "
                    "WHERE routine_id = %s AND revision_id = %s",
                    (routine_id, revision_id),
                )
                row = await cur.fetchone()
        except psycopg.errors.UndefinedTable:
            _log_missing_table_once()
            return None
        finally:
            await conn.close()
        if row is None:
            return None
        return cast(dict[str, Any], row["graph"])


class NullRoutineRevisionReader:
    """No-op reader used when no ``database_url`` is configured (dev/SQLite runs)."""

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None:
        return None

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        return None


def _log_missing_table_once() -> None:
    global _missing_table_logged
    if not _missing_table_logged:
        logger.warning(
            "routine_graph_revisions table not found; the runtime started before any "
            "backend installed the DDL. Routines will resolve unpinned (latest graph)."
        )
        _missing_table_logged = True
