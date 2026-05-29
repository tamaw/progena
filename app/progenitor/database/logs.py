"""Postgres-backed operational log sink."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


def _coerce_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


class PostgresLogStore:
    """Append/query operational logs in Postgres."""

    def __init__(self, database_url: str):
        self._pool = AsyncConnectionPool(database_url, open=False)

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    async def write_log(
        self,
        *,
        source: str,
        message: str,
        level: str = "info",
        payload: dict[str, Any] | None = None,
        timestamp: Any = None,
        relative_path: str | None = None,
    ) -> None:
        payload = payload or {}
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO progenitor_logs (timestamp, level, source, message, payload, relative_path)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    _coerce_timestamp(timestamp),
                    level.lower(),
                    source,
                    message,
                    Jsonb(payload),
                    relative_path,
                ),
            )

    async def query_logs(
        self,
        *,
        level: str | None = None,
        source: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_level = level.lower() if level else None
        search_like = f"%{search}%" if search else None
        if limit > 0:
            query = """
            SELECT timestamp, level, source, message, relative_path, payload
            FROM progenitor_logs
            WHERE (%s IS NULL OR level = %s)
              AND (%s IS NULL OR source = %s)
              AND (
                %s IS NULL
                OR message ILIKE %s
                OR payload::text ILIKE %s
                OR to_tsvector('simple', message) @@ plainto_tsquery('simple', %s)
              )
            ORDER BY timestamp DESC, id DESC
            LIMIT %s
            """
            params = [
                normalized_level,
                normalized_level,
                source,
                source,
                search,
                search_like,
                search_like,
                search,
                limit,
            ]
        else:
            query = """
            SELECT timestamp, level, source, message, relative_path, payload
            FROM progenitor_logs
            WHERE (%s IS NULL OR level = %s)
              AND (%s IS NULL OR source = %s)
              AND (
                %s IS NULL
                OR message ILIKE %s
                OR payload::text ILIKE %s
                OR to_tsvector('simple', message) @@ plainto_tsquery('simple', %s)
              )
            ORDER BY timestamp DESC, id DESC
            """
            params = [
                normalized_level,
                normalized_level,
                source,
                source,
                search,
                search_like,
                search_like,
                search,
            ]
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(query, params)
            rows = [dict(row) async for row in cursor]
        rows.reverse()
        return rows
