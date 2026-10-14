"""``search`` tool — hybrid semantic + keyword search over knowledge pages (RAG).

Never blocks on indexing: it answers from what the backend indexer has embedded
and reports, per base, which live pages are not searchable yet (computed now from
page content hashes, so it is accurate even before the indexer picks a change up).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from ..knowledge.embedding import embedder_for
from ..knowledge.index import indexed_shas_async, is_indexable, pending_paths, search_async
from ..knowledge.indexer_config import index_record, min_similarity, resolve_embedding_target
from ..knowledge.pages import KNOWLEDGE_MOUNT
from ..knowledge.schema import resolve_base_ref
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_NOTE = (
    "Read the pages before answering and cite their paths. Results only include pages that "
    "match by keyword or have similarity (0-1) at or above the min_similarity cut-off in "
    "/config/indexer.json, so no results means nothing relevant was found. Results marked "
    "deprecated are retired pages ranked last; prefer current pages. Pages listed as "
    "pending are not searchable yet; read them directly if they may be relevant."
)


def _database_url() -> str:
    return (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


class SearchArgs(BaseModel):
    query: str = Field(
        description="What to find in the knowledge pages (natural language or keywords)."
    )
    base: str | None = Field(
        default=None,
        description="Optional knowledge base id or name to search; all accessible bases when omitted.",
    )
    limit: int = Field(default=8, ge=1, le=20, description="Maximum number of pages to return.")


class SearchTool(BaseSystemTool):
    """Search knowledge pages; ``allowed_bases`` scopes an object agent to its grants."""

    def __init__(self, allowed_bases: frozenset[str] | None = None) -> None:
        self._allowed = allowed_bases

    @property
    def name(self) -> str:
        return "search"

    @property
    def description(self) -> str:
        return (
            "Semantic + keyword search over knowledge-base pages under /knowledge/. Returns the "
            "best-matching pages (path, heading, snippet). Use it to find relevant pages, then "
            "read_file them and cite their paths in your answer."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return SearchArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult.fail("Missing 'query'")
        sm = context.state_manager
        if sm is None:
            return ToolResult.fail("search needs the runtime state manager")
        database_url = _database_url()
        if not database_url:
            return ToolResult.fail("PROGENITOR_DATABASE_URL is not configured.")
        limit = max(1, min(int(args.get("limit") or 8), 20))

        bases = [
            base
            for base in (await sm.get_knowledge_config()).get("bases", [])
            if isinstance(base, dict) and isinstance(base.get("id"), str)
        ]
        if self._allowed is not None:
            bases = [base for base in bases if base["id"] in self._allowed]
        base_ids = [str(base["id"]) for base in bases]
        if args.get("base"):
            resolved = resolve_base_ref(bases, str(args["base"]))
            if resolved is None:
                return ToolResult.fail(
                    f"No accessible knowledge base matches {args['base']!r}"
                    + (
                        f" (granted: {', '.join(base_ids) or 'none'})"
                        if self._allowed is not None
                        else ""
                    )
                )
            base_ids = [resolved]
        if not base_ids:
            return ToolResult.ok(
                {"query": query, "results": [], "index": {}, "note": "No knowledge bases."}
            )

        settings = await sm.get_indexer_config()
        try:
            target = resolve_embedding_target(settings, await sm.get_model_profiles_config())
            vector = (await embedder_for(target).embed([query]))[0]
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:
            logger.error("search: embedding the query failed: %s", exc, exc_info=True)
            return ToolResult.fail(f"Embedding the query failed: {exc}")

        live: dict[str, dict[str, str]] = {base_id: {} for base_id in base_ids}
        for base_id, path, payload in await sm.list_knowledge_pages():
            if (
                base_id in live
                and isinstance(payload, dict)
                and isinstance(payload.get("content"), str)
                and is_indexable(path)
            ):
                live[base_id][path] = payload["content"]

        try:
            async with await psycopg.AsyncConnection.connect(
                database_url, autocommit=True
            ) as aconn:
                try:
                    rows = await search_async(
                        aconn,
                        query,
                        vector,
                        min_similarity=min_similarity(settings),
                        base_ids=base_ids,
                        limit=limit,
                    )
                    indexed = await indexed_shas_async(aconn, base_ids)
                except psycopg.errors.UndefinedTable:
                    rows, indexed = [], {}
        except Exception as exc:
            logger.error("search failed: %s", exc, exc_info=True)
            return ToolResult.fail(f"Knowledge search failed: {exc}")

        status_doc = await sm.get_knowledge_index()
        index: dict[str, Any] = {}
        for base_id in base_ids:
            record = index_record(status_doc, base_id) or {}
            index[base_id] = {
                "status": record.get("status") or "indexing",
                "pending": pending_paths(live[base_id], indexed.get(base_id, {})),
                **({"error": record["error"]} if record.get("error") else {}),
            }
        results = [
            {
                "path": f"{KNOWLEDGE_MOUNT}{row['base_id']}/{row['path']}",
                "title": row.get("title") or "",
                "heading": row.get("heading") or "",
                "snippet": row.get("snippet") or "",
                "similarity": round(float(row.get("similarity") or 0.0), 2),
                "keyword_match": bool(row.get("keyword_match")),
                **({"deprecated": True} if row.get("deprecated") else {}),
            }
            for row in rows
        ]
        return ToolResult.ok(
            {"query": query, "results": results, "index": index, "note": _NOTE},
            artifact={
                "kind": "search",
                "count": len(results),
                "summary": f"{len(results)} knowledge pages for '{query}'",
            },
        )
