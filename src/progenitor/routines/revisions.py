"""Async readers for immutable routine graph revisions.

The backend (``src/backend/storage/documents.py``) owns the DDL for the
``routine_graph_revisions`` table and the trigger that populates it. This
module only reads: it opens a short-lived ``psycopg.AsyncConnection`` per
fetch and closes it in ``finally``, because fetches are rare in steady state
(the routine executor caches compilation separately from resolving live
policy documents and their exact captured revisions).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

_missing_table_logged = False


class RoutineRevisionReader(Protocol):
    """Resolves routine graph revisions for the runtime's subagent proxy."""

    async def fetch_policy_revision_id(self, policy_id: str, document: dict[str, Any]) -> str: ...

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None: ...

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None: ...

    async def fetch_revision_id_for_content(
        self, routine_id: str, graph: dict[str, Any]
    ) -> str | None: ...


class PostgresRoutineRevisionReader:
    """Reads revisions from the backend-owned ``routine_graph_revisions`` table."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def fetch_policy_revision_id(self, policy_id: str, document: dict[str, Any]) -> str:
        async with (
            await psycopg.AsyncConnection.connect(self._database_url, autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(
                "SELECT revision_id FROM policy_revisions WHERE policy_id=%s AND document=%s::jsonb",
                (policy_id, Jsonb(document)),
            )
            row = await cur.fetchone()
        if row is None:
            raise ValueError(f"No captured revision for policy '{policy_id}'")
        return row[0]

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

    async def fetch_revision_id_for_content(
        self, routine_id: str, graph: dict[str, Any]
    ) -> str | None:
        """The revision_id whose captured content is byte-for-byte equal to
        ``graph``, if this exact content has ever been captured before.

        ``revision_id`` is a generated ``sha256(jsonb_send(graph))`` column
        (see document_store.py's DDL) -- rather than reproduce that exact
        hash algorithm in Python, this asks Postgres directly via ``jsonb``
        equality (which already normalizes key order/whitespace the same
        way the generated column's hash input does), so an unpinned run
        can tag itself with the correct revision_id for whatever content it
        actually compiled against, independent of `fetch_latest`'s
        capture-order-based (not content-based) notion of "latest".
        """
        conn = await psycopg.AsyncConnection[DictRow].connect(
            self._database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT revision_id FROM routine_graph_revisions "
                    "WHERE routine_id = %s AND graph = %s::jsonb LIMIT 1",
                    (routine_id, Jsonb(graph)),
                )
                row = await cur.fetchone()
        except psycopg.errors.UndefinedTable:
            _log_missing_table_once()
            return None
        finally:
            await conn.close()
        return None if row is None else cast(str, row["revision_id"])


class NullRoutineRevisionReader:
    """No-op reader used when no ``database_url`` is configured (dev/SQLite runs)."""

    async def fetch_policy_revision_id(self, policy_id: str, document: dict[str, Any]) -> str:
        from src.progenitor.settings.policies import policy_revision_id

        return policy_revision_id(document)

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None:
        return None

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        return None

    async def fetch_revision_id_for_content(
        self, routine_id: str, graph: dict[str, Any]
    ) -> str | None:
        return None


def _log_missing_table_once() -> None:
    global _missing_table_logged
    if not _missing_table_logged:
        logger.warning(
            "routine_graph_revisions table not found; the runtime started before any "
            "backend installed the DDL. Routines will resolve unpinned (latest graph)."
        )
        _missing_table_logged = True
