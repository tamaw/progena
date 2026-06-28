"""Postgres + pgvector storage for Progenitor knowledge bases.

A knowledge base is a named collection of documents whose content is embedded
into a pgvector column. The backend (sync psycopg) owns creation and ingestion;
the runtime ``knowledge_search`` tool (async psycopg) reads it back. Both share
the embedding function in :mod:`.embedding`, so vectors are comparable across
processes.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
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
        index_type      text NOT NULL DEFAULT 'Flat',
        tags            text[] NOT NULL DEFAULT '{{}}',
        connection_string text NOT NULL DEFAULT '',
        created_at      timestamptz NOT NULL DEFAULT now(),
        updated_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Backfill columns on bases created before these fields existed.
    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS connection_string text NOT NULL DEFAULT ''",
    "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS index_type text NOT NULL DEFAULT 'Flat'",
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
    # Documents ingested from a folder source carry its id so re-indexing can
    # replace just that source's documents.
    "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS source_id text",
    """
    CREATE TABLE IF NOT EXISTS knowledge_sources (
        id              text PRIMARY KEY,
        base_id         text NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
        path            text NOT NULL,
        status          text NOT NULL DEFAULT 'pending',
        document_count  integer NOT NULL DEFAULT 0,
        error           text NOT NULL DEFAULT '',
        last_indexed_at timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS knowledge_sources_base_idx ON knowledge_sources (base_id)",
)

# Folder indexing limits and the text file types we read. Binary/asset files and
# noisy build directories are skipped so indexing stays bounded and useful.
_INDEX_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".rst",
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".html",
        ".css",
        ".csv",
        ".log",
        ".sql",
        ".sh",
    }
)
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)
_MAX_INDEX_FILES = 500
_MAX_FILE_BYTES = 200_000
_MAX_DOC_CHARS = 20_000

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
    *,
    source_id: str | None = None,
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
                                                 metadata, embedding, source_id)
                VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                """,
                (base_id, collection, title, content, Jsonb(metadata), embedding, source_id),
            )
            inserted += 1
        cur.execute(
            "UPDATE knowledge_bases SET updated_at = %s WHERE id = %s",
            (datetime.now(UTC), base_id),
        )
    conn.commit()
    return inserted


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


def set_tags(conn: psycopg.Connection, base_id: str, tags: list[str]) -> dict[str, Any] | None:
    """Replace a base's tags with the given list (deduped, trimmed, order-kept)."""
    ensure_schema(conn)
    seen: set[str] = set()
    clean: list[str] = []
    for tag in tags:
        label = str(tag or "").strip()
        if label and label.lower() not in seen:
            seen.add(label.lower())
            clean.append(label)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE knowledge_bases SET tags = %s, updated_at = %s WHERE id = %s",
            (clean, datetime.now(UTC), base_id),
        )
        updated = cur.rowcount
    conn.commit()
    if not updated:
        return None
    return get_base(conn, base_id)


# --------------------------------------------------------------------------- #
# Folder sources (indexable document origins)
# --------------------------------------------------------------------------- #
def get_source(conn: psycopg.Connection, source_id: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM knowledge_sources WHERE id = %s", (source_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_sources(conn: psycopg.Connection, base_id: str) -> list[dict[str, Any]]:
    ensure_schema(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM knowledge_sources WHERE base_id = %s ORDER BY created_at",
            (base_id,),
        )
        return [dict(row) for row in cur.fetchall()]


def add_source(conn: psycopg.Connection, base_id: str, path: str) -> dict[str, Any]:
    ensure_schema(conn)
    source_id = f"src-{uuid.uuid4().hex[:10]}"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO knowledge_sources (id, base_id, path) VALUES (%s, %s, %s) RETURNING *",
            (source_id, base_id, str(path or "").strip()),
        )
        row = cur.fetchone()
    conn.commit()
    return dict(row) if row else {}


def delete_source(conn: psycopg.Connection, source_id: str) -> bool:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM knowledge_documents WHERE source_id = %s", (source_id,))
        cur.execute("DELETE FROM knowledge_sources WHERE id = %s", (source_id,))
        deleted = cur.rowcount
    conn.commit()
    return bool(deleted)


def _iter_index_files(root: Path) -> list[Path]:
    """Walk ``root`` for indexable text files, skipping junk dirs and big files."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            file = Path(dirpath) / name
            if file.suffix.lower() not in _INDEX_EXTENSIONS:
                continue
            try:
                if file.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            found.append(file)
            if len(found) >= _MAX_INDEX_FILES:
                return found
    return found


def _finish_source(
    conn: psycopg.Connection,
    source_id: str,
    *,
    status: str,
    error: str,
    count: int,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_sources
               SET status = %s, error = %s, document_count = %s, last_indexed_at = %s
             WHERE id = %s
            """,
            (status, error, count, datetime.now(UTC), source_id),
        )
    conn.commit()
    return get_source(conn, source_id)


def index_source(conn: psycopg.Connection, source_id: str) -> dict[str, Any] | None:
    """(Re)index a folder source: read its text files and embed them as documents.

    Documents from a prior index of the same source are removed first, so this is
    idempotent. Each file becomes one document in a collection named after the
    folder; ``source_id`` ties them back to this source.
    """
    ensure_schema(conn)
    source = get_source(conn, source_id)
    if source is None:
        return None

    with conn.cursor() as cur:
        cur.execute("DELETE FROM knowledge_documents WHERE source_id = %s", (source_id,))
    conn.commit()

    root = Path(str(source["path"])).expanduser()
    if not root.is_dir():
        return _finish_source(
            conn, source_id, status="error", error=f"Folder not found: {root}", count=0
        )

    collection = root.name or "folder"
    documents: list[dict[str, Any]] = []
    for file in _iter_index_files(root):
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not text.strip():
            continue
        documents.append(
            {
                "collection": collection,
                "title": str(file.relative_to(root)),
                "content": text[:_MAX_DOC_CHARS],
                "metadata": {"path": str(file)},
            }
        )

    try:
        count = add_documents(conn, str(source["base_id"]), documents, source_id=source_id)
    except Exception as exc:  # pragma: no cover - filesystem/db edge cases
        return _finish_source(conn, source_id, status="error", error=str(exc), count=0)
    return _finish_source(conn, source_id, status="indexed", error="", count=count)


def set_embedding(
    conn: psycopg.Connection,
    base_id: str,
    *,
    embedding_model: str,
    dimensions: int,
    metric: str,
    index_type: str,
) -> dict[str, Any] | None:
    """Update a base's embedding configuration (model/dimensions/metric/index)."""
    ensure_schema(conn)
    model = str(embedding_model or "").strip() or EMBED_MODEL_NAME
    dims = max(1, min(int(dimensions or EMBED_DIM), 4096))
    metric_value = str(metric or "").strip() or "cosine"
    index_value = str(index_type or "").strip() or "Flat"
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_bases
               SET embedding_model = %s, dimensions = %s, metric = %s,
                   index_type = %s, updated_at = %s
             WHERE id = %s
            """,
            (model, dims, metric_value, index_value, datetime.now(UTC), base_id),
        )
        updated = cur.rowcount
    conn.commit()
    if not updated:
        return None
    return get_base(conn, base_id)


def set_name(conn: psycopg.Connection, base_id: str, name: str) -> dict[str, Any] | None:
    """Rename a base. No-ops to None if the trimmed name is empty."""
    ensure_schema(conn)
    value = str(name or "").strip()
    if not value:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE knowledge_bases SET name = %s, updated_at = %s WHERE id = %s",
            (value, datetime.now(UTC), base_id),
        )
        updated = cur.rowcount
    conn.commit()
    if not updated:
        return None
    return get_base(conn, base_id)


def set_description(
    conn: psycopg.Connection, base_id: str, description: str
) -> dict[str, Any] | None:
    """Update a base's description."""
    ensure_schema(conn)
    value = str(description or "").strip()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE knowledge_bases SET description = %s, updated_at = %s WHERE id = %s",
            (value, datetime.now(UTC), base_id),
        )
        updated = cur.rowcount
    conn.commit()
    if not updated:
        return None
    return get_base(conn, base_id)


def set_connection_string(
    conn: psycopg.Connection, base_id: str, connection_string: str
) -> dict[str, Any] | None:
    """Record the connection string shown for this base (metadata, trimmed)."""
    ensure_schema(conn)
    value = str(connection_string or "").strip()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE knowledge_bases SET connection_string = %s, updated_at = %s WHERE id = %s",
            (value, datetime.now(UTC), base_id),
        )
        updated = cur.rowcount
    conn.commit()
    if not updated:
        return None
    return get_base(conn, base_id)


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
