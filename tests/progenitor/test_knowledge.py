"""Tests for the pgvector knowledge store and embedding."""

from __future__ import annotations

import os

import psycopg
import pytest

from app.progenitor.knowledge import embedding
from app.progenitor.knowledge import store as kstore

pytestmark = pytest.mark.filterwarnings("ignore")


def test_embedding_is_deterministic_and_normalised() -> None:
    a = embedding.embed_text("The capital of France is Paris")
    b = embedding.embed_text("The capital of France is Paris")
    assert a == b
    assert len(a) == embedding.EMBED_DIM
    norm = sum(value * value for value in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6


def test_embedding_similarity_rewards_overlap() -> None:
    def cosine(x: list[float], y: list[float]) -> float:
        return sum(i * j for i, j in zip(x, y, strict=True))

    query = embedding.embed_text("who owns project lighthouse")
    related = embedding.embed_text("Project Lighthouse is owned by Dana Reyes")
    unrelated = embedding.embed_text("bananas are a good source of potassium")
    assert cosine(query, related) > cosine(query, unrelated)


def _database_url() -> str | None:
    return (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip() or None


@pytest.mark.skipif(_database_url() is None, reason="requires PROGENITOR_DATABASE_URL")
def test_store_roundtrip_and_search() -> None:
    url = _database_url()
    assert url is not None
    with psycopg.connect(url) as conn:
        kstore.ensure_schema(conn)
        base = kstore.create_base(conn, name="pytest knowledge", description="temp")
        base_id = base["id"]
        try:
            documents = [
                {
                    "collection": "facts",
                    "title": "Project Lighthouse",
                    "content": "Project Lighthouse is the codename for the Q3 vector-search rollout.",
                },
                {
                    "collection": "facts",
                    "title": "Bananas",
                    "content": "Bananas are a good source of potassium.",
                },
            ]
            added = kstore.add_documents(conn, base_id, documents)
            assert added == len(documents)

            detail = kstore.get_base(conn, base_id)
            assert detail is not None
            assert detail["documents"] == added

            results = kstore.search(
                conn,
                "what is the codename for the q3 vector search rollout",
                base_id=base_id,
                limit=3,
            )
            assert results
            top = results[0]
            assert "Lighthouse" in top["content"]
            assert top["score"] > 0.0
        finally:
            kstore.delete_base(conn, base_id)
            assert kstore.get_base(conn, base_id) is None
