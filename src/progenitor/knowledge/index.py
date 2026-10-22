"""Search index over knowledge pages: chunking, ``knowledge_chunks`` SQL, hybrid search.

Concept pages are split by heading into chunks; each chunk is stored with its
embedding and a generated full-text vector. Search fuses the pgvector cosine
ranking with the full-text ranking (reciprocal rank fusion) and returns the best
chunk per page. The backend indexer writes this table; the runtime ``search`` tool
and the backend search route read it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src.progenitor.documents.front_matter import split_front_matter

from .pages import classify_page_path, is_deprecated

MAX_CHUNK_CHARS = 1500
SNIPPET_CHARS = 300
_CANDIDATES = 40
_RRF_K = 60
# A deprecated page's fused score is multiplied by this. RRF scores of chunks in the
# candidate set range ~1/(60+40)..2/61, so 0.05 puts every deprecated chunk below
# every current one: deprecated pages only surface when nothing current matches.
_DEPRECATED_SCORE_FACTOR = 0.05
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class PageChunk:
    heading: str
    text: str


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def page_sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def is_indexable(path: str) -> bool:
    """Concept pages are indexed (deprecated ones too, ranked last; see ``_SEARCH_SQL``)."""
    try:
        return classify_page_path(path) == "concept"
    except ValueError:
        return False


def _pack(paragraphs: list[str]) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > MAX_CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:MAX_CHUNK_CHARS])
            paragraph = paragraph[MAX_CHUNK_CHARS:]
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_page(content: str) -> tuple[dict[str, Any], list[PageChunk]]:
    """Split a concept page into heading sections packed to ``MAX_CHUNK_CHARS``.

    Returns ``(front_matter, chunks)``. A page whose body is empty still yields one
    chunk from its title/description so every page is searchable and tracked.
    """
    try:
        front_matter, body = split_front_matter(content)
    except ValueError:
        front_matter, body = None, content
    fm = front_matter or {}
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            sections.append((match.group(2), []))
        else:
            sections[-1][1].append(line)
    chunks: list[PageChunk] = []
    # A heading with no text of its own (e.g. "# Raft" right before "## Elections")
    # is carried into the next section's heading so it stays searchable.
    carried: list[str] = []
    for heading, lines in sections:
        paragraphs = [p.strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
        if not paragraphs:
            if heading:
                carried.append(heading)
            continue
        full_heading = " › ".join([*carried, heading] if heading else carried)
        carried = []
        chunks.extend(PageChunk(full_heading, text) for text in _pack(paragraphs))
    if carried:
        chunks.append(PageChunk(" › ".join(carried), " › ".join(carried)))
    if not chunks:
        summary = " — ".join(
            str(fm.get(key) or "").strip() for key in ("title", "description") if fm.get(key)
        )
        chunks.append(PageChunk("", summary or "(empty page)"))
    return fm, chunks


def embedding_text(front_matter: Mapping[str, Any], chunk: PageChunk) -> str:
    title = str(front_matter.get("title") or "").strip()
    description = str(front_matter.get("description") or "").strip()
    head = " — ".join(part for part in (title, description) if part)
    return "\n".join(part for part in (head, chunk.heading, chunk.text) if part)


def pending_paths(live: Mapping[str, str], indexed: Mapping[str, str]) -> list[str]:
    """Live indexable pages whose content isn't (yet) what the index holds."""
    return sorted(
        path
        for path, content in live.items()
        if is_indexable(path) and indexed.get(path) != page_sha(content)
    )


def to_pgvector(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{value:.7f}" for value in vector) + "]"


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def _column_dimensions(conn: psycopg.Connection) -> int | None:
    # Look only in the connection's own schema: with a search_path such as
    # "<schema>,public", an unqualified name would find (and the caller would
    # then drop) another schema's table.
    row = conn.execute(
        """
        SELECT a.atttypmod FROM pg_attribute a
        WHERE a.attrelid = to_regclass(quote_ident(current_schema()) || '.knowledge_chunks')
          AND a.attname = 'embedding'
        """
    ).fetchone()
    return int(row[0]) if row else None


def ensure_index_schema(conn: psycopg.Connection, dimensions: int) -> bool:
    """Create ``knowledge_chunks`` at ``dimensions``; recreate it on a size change.

    Returns True when the table was (re)created empty. Also drops the retired
    ``knowledge_documents`` table. Both act on the connection's current schema only.
    """
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    schema_row = conn.execute("SELECT current_schema()").fetchone()
    schema = str(schema_row[0]) if schema_row else "public"
    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(schema, "knowledge_documents"))
    )
    if _column_dimensions(conn) == dimensions:
        conn.execute(
            sql.SQL(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS deprecated boolean NOT NULL DEFAULT false"
            ).format(sql.Identifier(schema, "knowledge_chunks"))
        )
        conn.commit()
        return False
    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(schema, "knowledge_chunks"))
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE knowledge_chunks (
                base_id   text NOT NULL,
                path      text NOT NULL,
                chunk_no  integer NOT NULL,
                title     text NOT NULL DEFAULT '',
                heading   text NOT NULL DEFAULT '',
                content   text NOT NULL,
                page_sha  text NOT NULL,
                deprecated boolean NOT NULL DEFAULT false,
                embedding vector({dims}) NOT NULL,
                tsv       tsvector GENERATED ALWAYS AS (
                    to_tsvector('english', title || ' ' || heading || ' ' || content)
                ) STORED,
                PRIMARY KEY (base_id, path, chunk_no)
            )
            """
        ).format(dims=sql.Literal(dimensions))
    )
    conn.execute(
        "CREATE INDEX knowledge_chunks_embedding_hnsw ON knowledge_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    conn.execute("CREATE INDEX knowledge_chunks_tsv ON knowledge_chunks USING gin (tsv)")
    conn.commit()
    return True


# --------------------------------------------------------------------------- #
# Writes and bookkeeping (sync; backend indexer)
# --------------------------------------------------------------------------- #
def indexed_shas(conn: psycopg.Connection, base_id: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT path, min(page_sha) FROM knowledge_chunks WHERE base_id = %s GROUP BY path",
        (base_id,),
    ).fetchall()
    conn.commit()
    return {str(path): str(sha) for path, sha in rows}


def replace_page_chunks(
    conn: psycopg.Connection,
    base_id: str,
    path: str,
    sha: str,
    front_matter: Mapping[str, Any],
    chunks: list[PageChunk],
    vectors: list[list[float]],
) -> None:
    title = str(front_matter.get("title") or "")
    deprecated = is_deprecated(front_matter)
    conn.execute("DELETE FROM knowledge_chunks WHERE base_id = %s AND path = %s", (base_id, path))
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO knowledge_chunks
                (base_id, path, chunk_no, title, heading, content, page_sha, deprecated, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
            """,
            [
                (base_id, path, n, title, c.heading, c.text, sha, deprecated, to_pgvector(v))
                for n, (c, v) in enumerate(zip(chunks, vectors, strict=True))
            ],
        )
    conn.commit()


def delete_page_chunks(conn: psycopg.Connection, base_id: str, paths: Iterable[str]) -> None:
    paths = list(paths)
    if paths:
        conn.execute(
            "DELETE FROM knowledge_chunks WHERE base_id = %s AND path = ANY(%s)", (base_id, paths)
        )
        conn.commit()


def delete_other_bases(conn: psycopg.Connection, keep: Iterable[str]) -> list[str]:
    """Drop chunks of bases not in ``keep``; returns the removed base ids."""
    rows = conn.execute(
        "WITH gone AS (DELETE FROM knowledge_chunks WHERE NOT (base_id = ANY(%s)) "
        "RETURNING base_id) SELECT DISTINCT base_id FROM gone",
        (list(keep),),
    ).fetchall()
    conn.commit()
    return sorted(str(row[0]) for row in rows)


def chunk_count(conn: psycopg.Connection, base_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM knowledge_chunks WHERE base_id = %s", (base_id,)
    ).fetchone()
    conn.commit()
    return int(row[0]) if row else 0


# --------------------------------------------------------------------------- #
# Hybrid search (sync for the backend route, async for the runtime tool)
# --------------------------------------------------------------------------- #
_SEARCH_SQL: LiteralString = """
    WITH q AS (
        SELECT %(vec)s::vector AS v, websearch_to_tsquery('english', %(query)s) AS t
    ),
    vec AS (
        SELECT c.base_id, c.path, c.chunk_no,
               row_number() OVER (ORDER BY c.embedding <=> q.v) AS r
        FROM knowledge_chunks c, q
        WHERE %(bases)s::text[] IS NULL OR c.base_id = ANY(%(bases)s::text[])
        ORDER BY c.embedding <=> q.v
        LIMIT %(candidates)s
    ),
    txt AS (
        SELECT c.base_id, c.path, c.chunk_no,
               row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.t) DESC) AS r
        FROM knowledge_chunks c, q
        WHERE c.tsv @@ q.t
          AND (%(bases)s::text[] IS NULL OR c.base_id = ANY(%(bases)s::text[]))
        ORDER BY ts_rank_cd(c.tsv, q.t) DESC
        LIMIT %(candidates)s
    ),
    fused AS (
        SELECT base_id, path, chunk_no, sum(1.0 / (%(rrf_k)s + r)) AS score,
               bool_or(keyword) AS keyword
        FROM (
            SELECT base_id, path, chunk_no, r, false AS keyword FROM vec
            UNION ALL
            SELECT base_id, path, chunk_no, r, true AS keyword FROM txt
        ) ranked
        GROUP BY base_id, path, chunk_no
    ),
    -- Keep a chunk only if it matched by keyword or is similar enough by meaning.
    -- Deprecated pages stay searchable but rank below every current page.
    relevant AS (
        SELECT f.base_id, f.path, f.chunk_no, f.keyword, c.deprecated,
               f.score * CASE WHEN c.deprecated THEN %(deprecated_factor)s ELSE 1 END AS score,
               (1 - (c.embedding <=> q.v))::float AS similarity
        FROM fused f
        JOIN knowledge_chunks c USING (base_id, path, chunk_no), q
        WHERE f.keyword OR 1 - (c.embedding <=> q.v) >= %(min_similarity)s
    ),
    best AS (
        SELECT DISTINCT ON (base_id, path)
               base_id, path, chunk_no, score, keyword, similarity, deprecated
        FROM relevant
        ORDER BY base_id, path, score DESC
    )
    SELECT b.base_id, b.path, c.title, c.heading,
           left(c.content, %(snippet)s) AS snippet, b.score::float AS score,
           b.similarity, b.keyword AS keyword_match, b.deprecated
    FROM best b
    JOIN knowledge_chunks c USING (base_id, path, chunk_no)
    ORDER BY b.score DESC, b.base_id, b.path
    LIMIT %(limit)s
"""


def _search_params(
    query: str,
    vector: list[float],
    base_ids: Iterable[str] | None,
    limit: int,
    min_similarity: float,
) -> dict[str, Any]:
    return {
        "vec": to_pgvector(vector),
        "query": query,
        "bases": None if base_ids is None else list(base_ids),
        "candidates": _CANDIDATES,
        "rrf_k": _RRF_K,
        "snippet": SNIPPET_CHARS,
        "limit": limit,
        "min_similarity": min_similarity,
        "deprecated_factor": _DEPRECATED_SCORE_FACTOR,
    }


def search(
    conn: psycopg.Connection,
    query: str,
    vector: list[float],
    *,
    min_similarity: float,
    base_ids: Iterable[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    params = _search_params(query, vector, base_ids, limit, min_similarity)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_SEARCH_SQL, params)
        rows = [dict(row) for row in cur.fetchall()]
    conn.commit()
    return rows


async def search_async(
    aconn: psycopg.AsyncConnection,
    query: str,
    vector: list[float],
    *,
    min_similarity: float,
    base_ids: Iterable[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    params = _search_params(query, vector, base_ids, limit, min_similarity)
    async with aconn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SEARCH_SQL, params)
        return [dict(row) for row in await cur.fetchall()]


async def indexed_shas_async(
    aconn: psycopg.AsyncConnection, base_ids: Iterable[str]
) -> dict[str, dict[str, str]]:
    """``{base_id: {path: page_sha}}`` for freshness checks at query time."""
    async with aconn.cursor() as cur:
        await cur.execute(
            "SELECT base_id, path, min(page_sha) FROM knowledge_chunks "
            "WHERE base_id = ANY(%s) GROUP BY base_id, path",
            (list(base_ids),),
        )
        rows = await cur.fetchall()
    result: dict[str, dict[str, str]] = {}
    for base_id, path, sha in rows:
        result.setdefault(str(base_id), {})[str(path)] = str(sha)
    return result
