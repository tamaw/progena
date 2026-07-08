"""Knowledge search tool — recall facts from Postgres pgvector knowledge bases."""

from __future__ import annotations

import logging
import os

import psycopg
from pydantic import BaseModel, Field

from ..knowledge import load_bases_async, resolve_base_ref, search_async
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


def _database_url() -> str:
    return (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


class KnowledgeSearchArgs(BaseModel):
    query: str = Field(description="What to look up / recall from the knowledge base.")
    knowledge_base: str | None = Field(
        default=None,
        description=(
            "Optional knowledge base to search, by id or name. "
            "When omitted, all knowledge bases are searched."
        ),
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of passages to return.",
    )


class KnowledgeSearchTool(BaseSystemTool):
    """Semantic search over the Postgres pgvector knowledge stores."""

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "Search the Progenitor knowledge bases (Postgres pgvector stores) and "
            "return the most relevant stored passages. Use this to recall facts, "
            "documents, or notes that the user has saved in a knowledge base."
        )

    @property
    def args_model(self) -> type[BaseModel]:
        return KnowledgeSearchArgs

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        query = str(args.get("query") or context.user_input or "").strip()
        if not query:
            return ToolResult.fail("Missing 'query'")

        database_url = _database_url()
        if not database_url:
            return ToolResult.fail("PROGENITOR_DATABASE_URL is not configured.")

        base_ref = args.get("knowledge_base")
        limit = int(args.get("limit") or 5)
        limit = max(1, min(limit, 20))

        try:
            aconn = await psycopg.AsyncConnection.connect(database_url, autocommit=True)
        except Exception as exc:  # pragma: no cover - connection failures are environmental
            logger.error("knowledge_search: could not connect to Postgres: %s", exc)
            return ToolResult.fail(f"Could not connect to the knowledge database: {exc}")

        try:
            # Base names/settings live in the ``knowledge`` document, not a table,
            # so resolve the reference and map ids→names from there.
            bases = await load_bases_async(aconn)
            name_by_id = {str(base.get("id")): base.get("name") for base in bases}
            base_id: str | None = None
            if base_ref:
                base_id = resolve_base_ref(bases, str(base_ref))
                if base_id is None:
                    return ToolResult.ok(
                        {
                            "query": query,
                            "knowledge_base": base_ref,
                            "results": [],
                            "count": 0,
                            "note": f"No knowledge base matched '{base_ref}'.",
                        }
                    )

            rows = await search_async(aconn, query, base_id=base_id, limit=limit)
        except psycopg.errors.UndefinedTable:
            return ToolResult.ok(
                {
                    "query": query,
                    "results": [],
                    "count": 0,
                    "note": "No knowledge bases exist yet. Create one first.",
                }
            )
        except Exception as exc:
            logger.error("knowledge_search failed: %s", exc, exc_info=True)
            return ToolResult.fail(f"Knowledge search failed: {exc}")
        finally:
            await aconn.close()

        results = [
            {
                "knowledge_base": name_by_id.get(str(row.get("base_id"))),
                "knowledge_base_id": row.get("base_id"),
                "collection": row.get("collection"),
                "title": row.get("title"),
                "content": row.get("content"),
                "score": round(float(row.get("score") or 0.0), 4),
            }
            for row in rows
        ]
        return ToolResult.ok(
            {
                "query": query,
                "knowledge_base": base_ref,
                "count": len(results),
                "results": results,
            }
        )
