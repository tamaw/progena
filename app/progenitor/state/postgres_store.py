"""Postgres-backed state documents for the StateManager facade."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


class PostgresStateStore:
    """JSONB document store for StateManager state surfaces."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ):
        self._pool = AsyncConnectionPool(
            database_url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=False,
        )

    async def open(self) -> None:
        """Open the underlying connection pool."""
        await self._pool.open()

    async def close(self) -> None:
        """Close the underlying connection pool."""
        await self._pool.close()

    async def get_document(self, name: str, default: Any) -> Any:
        """Return one state document, creating it from default when absent."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                INSERT INTO progenitor_state_documents (name, payload)
                VALUES (%s, %s)
                ON CONFLICT (name) DO NOTHING
                """,
                (name, Jsonb(default)),
            )
            await cursor.execute(
                "SELECT payload FROM progenitor_state_documents WHERE name = %s",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return default
        return row["payload"]

    async def save_document(self, name: str, payload: Any) -> None:
        """Persist one state document."""
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO progenitor_state_documents (name, payload, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (name) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (name, Jsonb(payload)),
            )

    async def update_document(self, name: str, updates: dict[str, Any], default: Any) -> None:
        """Merge updates into one dict document."""
        current = await self.get_document(name, default)
        if not isinstance(current, dict):
            current = dict(default) if isinstance(default, dict) else {}
        current.update(updates)
        await self.save_document(name, current)
