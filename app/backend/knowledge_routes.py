"""Knowledge base API routes — Postgres pgvector stores for the Knowledge tab.

These endpoints let the frontend create knowledge bases, ingest documents,
and run vector searches. The same Postgres tables are read back by the runtime
``knowledge_search`` tool, so anything created here is immediately recallable by
the Progenitor agent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.progenitor.knowledge import (
    add_documents,
    add_source,
    create_base,
    delete_base,
    delete_source,
    get_base,
    index_source,
    list_bases,
    list_sources,
    resolve_base_id,
    search,
    set_connection_string,
    set_description,
    set_embedding,
    set_name,
    set_tags,
)

from .document_store import get_default_document_store, store_database_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui/knowledge", tags=["knowledge"])


def _database_url() -> str:
    url = store_database_url(get_default_document_store())
    if not url:
        raise HTTPException(status_code=503, detail="Knowledge database is not configured.")
    return url


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    # Normalise collection list (Postgres returns None when a base has no docs).
    if "collections" in out and out["collections"] is None:
        out["collections"] = []
    return out


class CreateBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    owner: str = Field(default="user", max_length=80)
    scope: str = Field(default="Mine", max_length=40)


class DocumentInput(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1)
    collection: str = Field(default="default", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AddDocumentsRequest(BaseModel):
    documents: list[DocumentInput] = Field(min_length=1)


class UpdateTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class UpdateConnectionRequest(BaseModel):
    connection_string: str = Field(default="", max_length=500)


class UpdateNameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class UpdateEmbeddingRequest(BaseModel):
    embedding_model: str = Field(default="", max_length=120)
    dimensions: int = Field(default=256, ge=1, le=4096)
    metric: str = Field(default="cosine", max_length=40)
    index_type: str = Field(default="Flat", max_length=40)


class UpdateDescriptionRequest(BaseModel):
    description: str = Field(default="", max_length=2000)


class AddSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


@router.get("")
async def list_knowledge_bases() -> dict[str, Any]:
    url = _database_url()

    def _work() -> list[dict[str, Any]]:
        with psycopg.connect(url) as conn:
            return list_bases(conn)

    bases = await asyncio.to_thread(_work)
    return {"bases": [_serialize(base) for base in bases]}


@router.post("")
async def create_knowledge_base(payload: CreateBaseRequest) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any]:
        with psycopg.connect(url) as conn:
            base = create_base(
                conn,
                name=payload.name,
                description=payload.description,
                owner=payload.owner,
                scope=payload.scope,
            )
            detail = get_base(conn, base["id"])
            return detail or base

    base = await asyncio.to_thread(_work)
    logger.info("Created knowledge base %s (%s)", base.get("id"), base.get("name"))
    return {"base": _serialize(base)}


@router.get("/{base_id}")
async def get_knowledge_base(base_id: str) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return get_base(conn, base_id)

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.delete("/{base_id}")
async def delete_knowledge_base(base_id: str) -> dict[str, Any]:
    url = _database_url()

    def _work() -> bool:
        with psycopg.connect(url) as conn:
            return delete_base(conn, base_id)

    deleted = await asyncio.to_thread(_work)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"deleted": True, "id": base_id}


@router.post("/{base_id}/documents")
async def add_knowledge_documents(base_id: str, payload: AddDocumentsRequest) -> dict[str, Any]:
    url = _database_url()
    documents = [doc.model_dump() for doc in payload.documents]

    def _work() -> tuple[int, dict[str, Any] | None]:
        with psycopg.connect(url) as conn:
            if get_base(conn, base_id) is None:
                return -1, None
            count = add_documents(conn, base_id, documents)
            return count, get_base(conn, base_id)

    count, base = await asyncio.to_thread(_work)
    if count < 0:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"added": count, "base": _serialize(base) if base else None}


@router.put("/{base_id}/embedding")
async def update_knowledge_embedding(
    base_id: str, payload: UpdateEmbeddingRequest
) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return set_embedding(
                conn,
                base_id,
                embedding_model=payload.embedding_model,
                dimensions=payload.dimensions,
                metric=payload.metric,
                index_type=payload.index_type,
            )

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.put("/{base_id}/name")
async def update_knowledge_name(base_id: str, payload: UpdateNameRequest) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return set_name(conn, base_id, payload.name)

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.put("/{base_id}/description")
async def update_knowledge_description(
    base_id: str, payload: UpdateDescriptionRequest
) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return set_description(conn, base_id, payload.description)

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.put("/{base_id}/tags")
async def update_knowledge_tags(base_id: str, payload: UpdateTagsRequest) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return set_tags(conn, base_id, payload.tags)

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.put("/{base_id}/connection")
async def update_knowledge_connection(
    base_id: str, payload: UpdateConnectionRequest
) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return set_connection_string(conn, base_id, payload.connection_string)

    base = await asyncio.to_thread(_work)
    if base is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"base": _serialize(base)}


@router.get("/{base_id}/sources")
async def list_knowledge_sources(base_id: str) -> dict[str, Any]:
    url = _database_url()

    def _work() -> list[dict[str, Any]]:
        with psycopg.connect(url) as conn:
            return list_sources(conn, base_id)

    sources = await asyncio.to_thread(_work)
    return {"sources": [_serialize(source) for source in sources]}


@router.post("/{base_id}/sources")
async def add_knowledge_source(base_id: str, payload: AddSourceRequest) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            if get_base(conn, base_id) is None:
                return None
            source = add_source(conn, base_id, payload.path)
            # Index immediately so the folder's files are searchable right away.
            return index_source(conn, source["id"]) or source

    source = await asyncio.to_thread(_work)
    if source is None:
        raise HTTPException(status_code=404, detail="Knowledge base not found.")
    return {"source": _serialize(source)}


@router.post("/{base_id}/sources/{source_id}/index")
async def reindex_knowledge_source(base_id: str, source_id: str) -> dict[str, Any]:
    url = _database_url()

    def _work() -> dict[str, Any] | None:
        with psycopg.connect(url) as conn:
            return index_source(conn, source_id)

    source = await asyncio.to_thread(_work)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"source": _serialize(source)}


@router.delete("/{base_id}/sources/{source_id}")
async def delete_knowledge_source(base_id: str, source_id: str) -> dict[str, Any]:
    url = _database_url()

    def _work() -> bool:
        with psycopg.connect(url) as conn:
            return delete_source(conn, source_id)

    deleted = await asyncio.to_thread(_work)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found.")
    return {"deleted": True, "id": source_id}


@router.get("/{base_id}/search")
async def search_knowledge_base(
    base_id: str,
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict[str, Any]:
    url = _database_url()

    def _work() -> list[dict[str, Any]]:
        with psycopg.connect(url) as conn:
            resolved = resolve_base_id(conn, base_id)
            if resolved is None:
                raise HTTPException(status_code=404, detail="Knowledge base not found.")
            return search(conn, q, base_id=resolved, limit=limit)

    rows = await asyncio.to_thread(_work)
    return {"query": q, "count": len(rows), "results": [_serialize(row) for row in rows]}
