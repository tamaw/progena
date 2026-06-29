"""Postgres + pgvector storage for Progenitor knowledge bases.

Knowledge-base *settings* live in the ``knowledge`` document of the shared state
store (validated by :mod:`.schema`); this module owns the part that cannot live in
JSON: the ``knowledge_documents`` table whose content is embedded into a pgvector
column. The backend (sync psycopg) ingests/searches it and the runtime
``knowledge_search`` tool (async psycopg) reads it back. Both share the embedding
function in :mod:`.embedding`, so vectors are comparable across processes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .embedding import embed_text, to_pgvector
from .schema import (
    KnowledgeBaseModel,
    generate_base_id,
    now_isoformat,
    slugify,
)

# CREATE EXTENSION is intentionally first; the documents table assumes the
# ``vector`` type exists. Both statements are idempotent so callers can run this
# on each access. The base *settings* are not a table — they live in the
# ``knowledge`` state document — so ``base_id`` here is a plain column (no FK).
SCHEMA_STATEMENTS: tuple[LiteralString, ...] = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    # Base settings moved to the ``knowledge`` state document, so the legacy
    # tables are dropped. CASCADE also removes the old foreign key that
    # ``knowledge_documents.base_id`` had on ``knowledge_bases``.
    "DROP TABLE IF EXISTS knowledge_sources CASCADE",
    "DROP TABLE IF EXISTS knowledge_bases CASCADE",
    """
    CREATE TABLE IF NOT EXISTS knowledge_documents (
        id          bigserial PRIMARY KEY,
        base_id     text NOT NULL,
        collection  text NOT NULL DEFAULT 'default',
        title       text NOT NULL DEFAULT '',
        content     text NOT NULL,
        metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
        embedding   vector(256),
        source_id   text,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Pre-existing document tables may predate the source_id column.
    "ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS source_id text",
    "CREATE INDEX IF NOT EXISTS knowledge_documents_base_idx ON knowledge_documents (base_id)",
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

_SEARCH_SQL: LiteralString = """
    SELECT
        d.base_id,
        d.collection,
        d.title,
        d.content,
        d.metadata,
        1.0 - (d.embedding <=> %(vec)s::vector) AS score
    FROM knowledge_documents AS d
    WHERE d.embedding IS NOT NULL
      AND (%(base_id)s::text IS NULL OR d.base_id = %(base_id)s::text)
    ORDER BY d.embedding <=> %(vec)s::vector
    LIMIT %(limit)s
"""

__all__ = [
    "SCHEMA_STATEMENTS",
    "ensure_schema",
    "new_base",
    "find_base",
    "resolve_base_ref",
    "add_documents",
    "delete_documents_for_base",
    "delete_documents_for_source",
    "base_document_stats",
    "index_folder",
    "search",
    "search_async",
    "load_bases_async",
    "slugify",
    "generate_base_id",
]


# --------------------------------------------------------------------------- #
# Pure settings helpers (operate on the document payload's ``bases`` list)
# --------------------------------------------------------------------------- #
def new_base(
    *,
    name: str,
    description: str = "",
    owner: str = "user",
    scope: str = "Mine",
) -> dict[str, Any]:
    """Build a fresh base settings dict with defaults, an id, and timestamps."""
    timestamp = now_isoformat()
    entry = KnowledgeBaseModel(
        name=name, description=description, owner=owner, scope=scope
    ).model_dump(mode="json")
    entry["id"] = generate_base_id(name)
    entry["created_at"] = timestamp
    entry["updated_at"] = timestamp
    return entry


def find_base(bases: list[dict[str, Any]], base_id: str) -> dict[str, Any] | None:
    """Return the base with ``base_id`` from a bases list, or None."""
    for base in bases:
        if str(base.get("id")) == base_id:
            return base
    return None


def resolve_base_ref(bases: list[dict[str, Any]], ref: str) -> str | None:
    """Resolve a base reference (exact id, else case-insensitive name) to an id."""
    needle = str(ref or "").strip()
    if not needle:
        return None
    for base in bases:
        if str(base.get("id")) == needle:
            return str(base["id"])
    lowered = needle.lower()
    for base in bases:
        if str(base.get("name", "")).strip().lower() == lowered:
            return str(base["id"])
    return None


# --------------------------------------------------------------------------- #
# Document SQL (sync — backend ingestion / search)
# --------------------------------------------------------------------------- #
def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for statement in SCHEMA_STATEMENTS:
            cur.execute(statement)
    conn.commit()


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
    conn.commit()
    return inserted


def delete_documents_for_base(conn: psycopg.Connection, base_id: str) -> int:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM knowledge_documents WHERE base_id = %s", (base_id,))
        deleted = cur.rowcount
    conn.commit()
    return int(deleted)


def delete_documents_for_source(conn: psycopg.Connection, source_id: str) -> int:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM knowledge_documents WHERE source_id = %s", (source_id,))
        deleted = cur.rowcount
    conn.commit()
    return int(deleted)


def base_document_stats(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    """Return per-base derived stats: ``{base_id: {documents, collections}}``."""
    ensure_schema(conn)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT base_id,
                   COUNT(*) AS documents,
                   ARRAY_AGG(DISTINCT collection) AS collections
            FROM knowledge_documents
            GROUP BY base_id
            """
        )
        rows = cur.fetchall()
    return {
        str(row["base_id"]): {
            "documents": int(row["documents"]),
            "collections": list(row["collections"] or []),
        }
        for row in rows
    }


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
# Folder indexing
# --------------------------------------------------------------------------- #
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


def index_folder(
    conn: psycopg.Connection,
    *,
    base_id: str,
    source_id: str,
    path: str,
) -> dict[str, Any]:
    """(Re)index a folder source: read its text files and embed them as documents.

    Documents from a prior index of the same source are removed first, so this is
    idempotent. Returns the source's operational fields (status / document_count /
    error / last_indexed_at) for the caller to merge back into the document.
    """
    delete_documents_for_source(conn, source_id)

    root = Path(str(path or "")).expanduser()
    if not root.is_dir():
        return {
            "status": "error",
            "document_count": 0,
            "error": f"Folder not found: {root}",
            "last_indexed_at": now_isoformat(),
        }

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
        count = add_documents(conn, base_id, documents, source_id=source_id)
    except Exception as exc:  # pragma: no cover - filesystem/db edge cases
        return {
            "status": "error",
            "document_count": 0,
            "error": str(exc),
            "last_indexed_at": now_isoformat(),
        }
    return {
        "status": "indexed",
        "document_count": count,
        "error": "",
        "last_indexed_at": now_isoformat(),
    }


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


async def load_bases_async(aconn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    """Read the ``knowledge`` document's bases list from the shared ``store`` table."""
    async with aconn.cursor() as cur:
        await cur.execute(
            "SELECT value->'payload'->'bases' FROM store "
            "WHERE prefix = 'progenitor.state' AND key = 'knowledge'"
        )
        row = await cur.fetchone()
    if not row or not row[0]:
        return []
    bases = row[0]
    return [dict(base) for base in bases] if isinstance(bases, list) else []
