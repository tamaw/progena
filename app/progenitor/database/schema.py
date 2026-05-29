"""Schema bootstrap for Progenitor-owned Postgres tables."""

from __future__ import annotations

APPLICATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS progenitor_state_documents (
    name TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL DEFAULT 'info',
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    relative_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_progenitor_logs_timestamp
    ON progenitor_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_progenitor_logs_source
    ON progenitor_logs (source);
CREATE INDEX IF NOT EXISTS idx_progenitor_logs_level
    ON progenitor_logs (level);
CREATE INDEX IF NOT EXISTS idx_progenitor_logs_payload
    ON progenitor_logs USING GIN (payload);
CREATE INDEX IF NOT EXISTS idx_progenitor_logs_message_fts
    ON progenitor_logs USING GIN (to_tsvector('simple', message));
"""


async def setup_application_schema(database_url: str) -> None:
    """Create Progenitor-owned tables if they do not already exist."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(database_url) as conn, conn.cursor() as cursor:
        await cursor.execute("SELECT pg_advisory_lock(hashtext('progenitor_application_schema'))")
        try:
            await cursor.execute(APPLICATION_SCHEMA_SQL)
        finally:
            await cursor.execute(
                "SELECT pg_advisory_unlock(hashtext('progenitor_application_schema'))"
            )
