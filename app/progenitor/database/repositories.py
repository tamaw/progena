"""Repositories for Progenitor-owned Postgres tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool


@dataclass(slots=True)
class PersistedAttachment:
    """Attachment row loaded from Postgres."""

    id: str
    conversation_id: str | None
    message_id: str | None
    filename: str
    media_type: str | None
    size_bytes: int
    checksum: str | None
    storage_mode: str
    storage_key: str | None
    content: bytes | None
    metadata: dict[str, Any]


class ProgenitorRepository:
    """Async repository for application tables not owned by LangGraph."""

    def __init__(self, database_url: str):
        self._pool = AsyncConnectionPool(database_url, open=False)

    async def open(self) -> None:
        """Open the connection pool."""
        await self._pool.open()

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

    async def upsert_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create or touch a conversation."""
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO progenitor_conversations (id, title, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    title = COALESCE(EXCLUDED.title, progenitor_conversations.title),
                    metadata = progenitor_conversations.metadata || EXCLUDED.metadata,
                    updated_at = now()
                """,
                (conversation_id, title, Jsonb(metadata or {})),
            )

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        checkpoint_thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a message to a conversation with a per-conversation sequence."""
        await self.upsert_conversation(conversation_id)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO progenitor_messages (
                    id,
                    conversation_id,
                    role,
                    content,
                    sequence,
                    checkpoint_thread_id,
                    metadata
                )
                SELECT
                    %s,
                    %s,
                    %s,
                    %s,
                    COALESCE(MAX(sequence), 0) + 1,
                    %s,
                    %s
                FROM progenitor_messages
                WHERE conversation_id = %s
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    checkpoint_thread_id,
                    Jsonb(metadata or {}),
                    conversation_id,
                ),
            )
            await conn.execute(
                "UPDATE progenitor_conversations SET updated_at = now() WHERE id = %s",
                (conversation_id,),
            )

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """Return product-facing messages for a conversation."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT id, role, content, sequence, checkpoint_thread_id, metadata, created_at
                FROM progenitor_messages
                WHERE conversation_id = %s
                ORDER BY sequence ASC
                """,
                (conversation_id,),
            )
            return [dict(row) async for row in cursor]

    async def clear_conversation(self, conversation_id: str) -> None:
        """Clear product-facing messages for a conversation."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM progenitor_messages WHERE conversation_id = %s",
                (conversation_id,),
            )
            await conn.execute(
                "UPDATE progenitor_conversations SET updated_at = now() WHERE id = %s",
                (conversation_id,),
            )

    async def add_attachment(
        self,
        *,
        attachment_id: str,
        filename: str,
        conversation_id: str | None = None,
        message_id: str | None = None,
        media_type: str | None = None,
        size_bytes: int = 0,
        checksum: str | None = None,
        storage_mode: str = "postgres",
        storage_key: str | None = None,
        content: bytes | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist attachment metadata and optional bytes."""
        if conversation_id:
            await self.upsert_conversation(conversation_id)
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO progenitor_attachments (
                    id,
                    conversation_id,
                    message_id,
                    filename,
                    media_type,
                    size_bytes,
                    checksum,
                    storage_mode,
                    storage_key,
                    content,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    conversation_id = EXCLUDED.conversation_id,
                    message_id = EXCLUDED.message_id,
                    filename = EXCLUDED.filename,
                    media_type = EXCLUDED.media_type,
                    size_bytes = EXCLUDED.size_bytes,
                    checksum = EXCLUDED.checksum,
                    storage_mode = EXCLUDED.storage_mode,
                    storage_key = EXCLUDED.storage_key,
                    content = EXCLUDED.content,
                    metadata = EXCLUDED.metadata
                """,
                (
                    attachment_id,
                    conversation_id,
                    message_id,
                    filename,
                    media_type,
                    size_bytes,
                    checksum,
                    storage_mode,
                    storage_key,
                    content,
                    Jsonb(metadata or {}),
                ),
            )

    async def get_attachment(self, attachment_id: str) -> PersistedAttachment | None:
        """Load one attachment by id."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                """
                SELECT id,
                       conversation_id,
                       message_id,
                       filename,
                       media_type,
                       size_bytes,
                       checksum,
                       storage_mode,
                       storage_key,
                       content,
                       metadata
                FROM progenitor_attachments
                WHERE id = %s
                """,
                (attachment_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return PersistedAttachment(**dict(row))

    async def save_session(
        self,
        session_id: str,
        *,
        payload: dict[str, Any],
        user_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        """Persist one backend web session."""
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO progenitor_sessions (id, user_id, payload, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    payload = EXCLUDED.payload,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """,
                (session_id, user_id, Jsonb(payload), expires_at),
            )

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Load all backend web session payloads."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SELECT payload FROM progenitor_sessions ORDER BY created_at ASC")
            return [dict(row["payload"]) async for row in cursor]

    async def delete_session(self, session_id: str) -> None:
        """Delete one backend web session."""
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM progenitor_sessions WHERE id = %s", (session_id,))

    async def delete_expired_sessions(self) -> int:
        """Delete expired backend web sessions."""
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM progenitor_sessions WHERE expires_at IS NOT NULL AND expires_at < now()"
            )
            return result.rowcount or 0


@asynccontextmanager
async def open_repository(database_url: str) -> AsyncIterator[ProgenitorRepository]:
    """Open and close a repository around an async context."""
    repository = ProgenitorRepository(database_url)
    await repository.open()
    try:
        yield repository
    finally:
        await repository.close()
