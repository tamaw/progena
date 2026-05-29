"""Schema bootstrap for Progenitor-owned Postgres tables."""

from __future__ import annotations

APPLICATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS progenitor_conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES progenitor_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    checkpoint_thread_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, sequence)
);

CREATE TABLE IF NOT EXISTS progenitor_attachments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES progenitor_conversations(id) ON DELETE CASCADE,
    message_id TEXT REFERENCES progenitor_messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    media_type TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    checksum TEXT,
    storage_mode TEXT NOT NULL DEFAULT 'postgres',
    storage_key TEXT,
    content BYTEA,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_scheduled_tasks (
    id TEXT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT true,
    due_at TIMESTAMPTZ,
    cadence TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_build_items (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    kind TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_state_documents (
    name TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS progenitor_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    payload JSONB NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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

CREATE INDEX IF NOT EXISTS idx_progenitor_messages_conversation_sequence
    ON progenitor_messages (conversation_id, sequence);
CREATE INDEX IF NOT EXISTS idx_progenitor_scheduled_tasks_due
    ON progenitor_scheduled_tasks (enabled, due_at)
    WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_progenitor_build_items_status
    ON progenitor_build_items (status);
CREATE INDEX IF NOT EXISTS idx_progenitor_sessions_user
    ON progenitor_sessions (user_id)
    WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_progenitor_sessions_expires
    ON progenitor_sessions (expires_at);
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
