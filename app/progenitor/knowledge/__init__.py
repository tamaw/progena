"""Postgres + pgvector knowledge base storage and retrieval."""

from .embedding import EMBED_DIM, EMBED_MODEL_NAME, embed_text, to_pgvector
from .store import (
    SEED_DOCUMENTS,
    add_documents,
    create_base,
    delete_base,
    ensure_schema,
    generate_base_id,
    get_base,
    list_bases,
    resolve_base_id,
    resolve_base_id_async,
    search,
    search_async,
    seed_base,
)

__all__ = [
    "EMBED_DIM",
    "EMBED_MODEL_NAME",
    "embed_text",
    "to_pgvector",
    "SEED_DOCUMENTS",
    "add_documents",
    "create_base",
    "delete_base",
    "ensure_schema",
    "generate_base_id",
    "get_base",
    "list_bases",
    "resolve_base_id",
    "resolve_base_id_async",
    "search",
    "search_async",
    "seed_base",
]
