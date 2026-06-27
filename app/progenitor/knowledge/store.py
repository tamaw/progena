"""Postgres + pgvector storage for Progenitor knowledge bases.

A knowledge base is a named collection of documents whose content is embedded
into a pgvector column. The backend (sync psycopg) owns creation and ingestion;
the runtime ``knowledge_search`` tool (async psycopg) reads it back. Both share
the embedding function in :mod:`.embedding`, so vectors are comparable across
processes.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .embedding import EMBED_DIM, EMBED_MODEL_NAME, embed_text, to_pgvector

# CREATE EXTENSION is intentionally first; the rest assume the ``vector`` type
# exists. Every statement is idempotent so callers can run this on each access.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    f"""
    CREATE TABLE IF NOT EXISTS knowledge_bases (
        id              text PRIMARY KEY,
        name            text NOT NULL,
        description     text NOT NULL DEFAULT '',
        owner           text NOT NULL DEFAULT 'user',
        scope           text NOT NULL DEFAULT 'Mine',
        status          text NOT NULL DEFAULT 'connected',
        embedding_model text NOT NULL DEFAULT '{EMBED_MODEL_NAME}',
        dimensions      integer NOT NULL DEFAULT {EMBED_DIM},
        metric          text NOT NULL DEFAULT 'cosine',
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS knowledge_documents (
        id          bigserial PRIMARY KEY,
        base_id     text NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        collection  text NOT NULL DEFAULT 'default',
        title       text NOT NULL DEFAULT '',
        content     text NOT NULL,
        metadata    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        embedding   vector({EMBED_DIM}),
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS knowledge_documents_base_idx ON knowledge_documents (base_id)",
)

# A small, distinctive mock corpus seeded into every new base so the agent has
# something concrete to recall. "Project Lighthouse"/"Dana Reyes" are invented
# facts the model cannot already know, which makes a successful recall proof that
# the tool actually read from the store.
SEED_DOCUMENTS: tuple[dict[str, str], ...] = (
    {
        "collection": "facts",
        "title": "Project Lighthouse",
        "content": (
            "Project Lighthouse is the internal codename for Progenitor's Q3 2026 "
            "vector-search rollout. It is owned by Dana Reyes and targets a "
            "September 2026 launch."
        ),
    },
    {
        "collection": "facts",
        "title": "Knowledge storage",
        "content": (
            "The Progenitor knowledge subsystem stores document embeddings in "
            "Postgres using the pgvector extension and ranks results by cosine "
            "similarity."
        ),
    },
    {
        "collection": "facts",
        "title": "Attaching knowledge to agents",
        "content": (
            "A knowledge base can be attached to a Progenitor agent as the "
            "knowledge_search retrieval tool so the agent can recall stored facts "
            "during a conversation."
        ),
    },
    {
        "collection": "facts",
        "title": "Embedding model",
        "content": (
            "The mock embedding model used for local development is "
            "progenitor-hash-256, a deterministic 256-dimensional hashing embedder."
        ),
    },
    {
        "collection": "people",
        "title": "Dana Reyes",
        "content": (
            "Dana Reyes leads the Knowledge Platform team and can be reached at "
            "dana.reyes@progenitor.local."
        ),
    },
)

_SEARCH_SQL = """
    SELECT
        d.base_id,
        b.name AS base_name,
        d.collection,
        d.title,
        d.content,
        d.metadata,
        1.0 - (d.embedding <=> %(vec)s::vector) AS score
    FROM knowledge_documents AS d
    JOIN knowledge_bases AS b ON b.id = d.base_id
    WHERE d.embedding IS NOT NULL
      AND (%(base_id)s::text IS NULL OR d.base_id = %(base_id)s::text)
    ORDER BY d.embedding <=> %(vec)s::vector
    LIMIT %(limit)s
"""


def slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return base or "kb"


def generate_base_id(name: str) -> str:
    return f"{slugify(name)[:32]}-{uuid.uuid4().hex[:6]}"


# --------------------------------------------------------------------------- #
# Sync API (backend ingestion / CRUD)
# --------------------------------------------------------------------------- #
def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for statement in SCHEMA_STATEMENTS:
            cur.execute(statement)
    conn.commit()


def create_base(
    conn: psycopg.Connection,
    *,
    name: str,
    description: str = "",
    owner: str = "user",
    scope: str = "Mine",
    base_id: str | None = None,
) -> dict[str, Any]:
    ensure_schema(conn)
    resolved_id = base_id or generate_base_id(name)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO knowledge_bases (id, name, description, owner, scope, status,
                                         embedding_model, dimensions, metric)
            VALUES (%s, %s, %s, %s, %s, 'connected', %s, %s, 'cosine')
            RETURNING *
            """,
            (resolved_id, name, description, owner, scope, EMBED_MODEL_NAME, EMBED_DIM),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else {}


def add_documents(
    conn: psycopg.Connection,
    base_id: str,
    documents: list[dict[str, Any]],
) -> int:
    if not documents:
        return 0
    ensure_schema(conn)
    inserted = 0
    with conn.cursor() as cur:
        for doc in documents:
            content = str(doc.get("content") or "").strip()
            if not content:
                continue
            title = str(doc.get("title") or "")
            collection = str(doc.get("collection") or "default")
            metadata = doc.get("metadata") or {}
            embedding = to_pgvector(embed_text(f"{title}\n{content}"))
            cur.execute(
                """
                INSERT INTO knowledge_documents (base_id, collection, title, content,
                                                 metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::vector)
                """,
                (base_id, collection, title, content, Jsonb(metadata), embedding),
            )
            inserted += 1
        cur.execute(
            "UPDATE knowledge_bases SET updated_at = %s WHERE id = %s",
            (datetime.now(UTC), base_id),
        )
    conn.commit()
    return inserted


def seed_base(conn: psycopg.Connection, base_id: str) -> int:
    """Insert the distinctive mock corpus into a base."""
    return add_documents(conn, base_id, [dict(doc) for doc in SEED_DOCUMENTS])


def _base_stats_sql() -> str:
    return """
        SELECT
            b.*,
            COALESCE(d.doc_count, 0) AS documents,
            COALESCE(d.collections, ARRAY[]::text[]) AS collections
        FROM knowledge_bases AS b
        LEFT JOIN (
            SELECT base_id,
                   COUNT(*) AS doc_count,
                   ARRAY_AGG(DISTINCT collection) AS collections
            FROM knowledge_documents
            GROUP BY base_id
        ) AS d ON d.base_id = b.id
    """


def list_bases(conn: psycopg.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_base_stats_sql() + " ORDER BY b.updated_at DESC")
        return [dict(row) for row in cur.fetchall()]


def get_base(conn: psycopg.Connection, base_id: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_base_stats_sql() + " WHERE b.id = %s", (base_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def delete_base(conn: psycopg.Connection, base_id: str) -> bool:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM knowledge_bases WHERE id = %s", (base_id,))
        deleted = cur.rowcount
    conn.commit()
    return bool(deleted)


def resolve_base_id(conn: psycopg.Connection, base: str) -> str | None:
    """Resolve a base reference (exact id, else case-insensitive name) to an id."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM knowledge_bases WHERE id = %s", (base,))
        row = cur.fetchone()
        if row:
            return str(row[0])
        cur.execute("SELECT id FROM knowledge_bases WHERE name ILIKE %s LIMIT 1", (base,))
        row = cur.fetchone()
    return str(row[0]) if row else None


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    base_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    vec = to_pgvector(embed_text(query))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SEARCH_SQL, {"vec": vec, "base_id": base_id, "limit": limit})
        return [dict(row) for row in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Async API (runtime tool)
# --------------------------------------------------------------------------- #
async def search_async(
    aconn: psycopg.AsyncConnection,
    query: str,
    *,
    base_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    vec = to_pgvector(embed_text(query))
    async with aconn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SEARCH_SQL, {"vec": vec, "base_id": base_id, "limit": limit})
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def resolve_base_id_async(aconn: psycopg.AsyncConnection, base: str) -> str | None:
    async with aconn.cursor() as cur:
        await cur.execute("SELECT id FROM knowledge_bases WHERE id = %s", (base,))
        row = await cur.fetchone()
        if row:
            return str(row[0])
        await cur.execute("SELECT id FROM knowledge_bases WHERE name ILIKE %s LIMIT 1", (base,))
        row = await cur.fetchone()
    return str(row[0]) if row else None
