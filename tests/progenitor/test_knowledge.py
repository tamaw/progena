"""Tests for the pgvector knowledge store, schema, and embedding."""

from __future__ import annotations

import os

import psycopg
import pytest

from app.progenitor.knowledge import embedding
from app.progenitor.knowledge import schema as kschema
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


# --------------------------------------------------------------------------- #
# Schema validation (no database needed)
# --------------------------------------------------------------------------- #
def test_validate_payload_normalizes_and_fills() -> None:
    result = kschema.validate_knowledge_payload(
        {
            "bases": [
                {
                    "name": "Notes",
                    "tags": ["a", "a", " b "],
                    "metric": "COSINE",
                    "index_type": "hnsw",
                    "documents": 99,  # derived/unknown field is dropped
                    "sources": [{"path": "/tmp/docs"}],
                }
            ]
        }
    )
    base = result["bases"][0]
    assert base["id"]  # generated from the name
    assert base["created_at"] and base["updated_at"]
    assert base["tags"] == ["a", "b"]
    assert base["metric"] == "cosine"
    assert base["index_type"] == "HNSW"
    assert "documents" not in base
    assert base["sources"][0]["id"].startswith("src-")


def test_validate_payload_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload({"bases": [{"name": "x", "metric": "bogus"}]})
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload({"bases": [{"name": "x", "index_type": "tree"}]})
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload({"bases": [{"description": "no name"}]})
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload(["not", "an", "object"])


def test_validate_payload_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload(
            {"bases": [{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}]}
        )


def test_validate_document_rejects_bad_json() -> None:
    with pytest.raises(ValueError):
        kschema.validate_knowledge_document("{not json}")


def test_pure_helpers() -> None:
    base = kstore.new_base(name="My KB", description="hi")
    assert base["id"] and base["name"] == "My KB"
    bases = [base]
    assert kstore.find_base(bases, base["id"]) is base
    assert kstore.find_base(bases, "missing") is None
    assert kstore.resolve_base_ref(bases, base["id"]) == base["id"]
    assert kstore.resolve_base_ref(bases, "my kb") == base["id"]  # by name, case-insensitive
    assert kstore.resolve_base_ref(bases, "nope") is None


# --------------------------------------------------------------------------- #
# Document SQL (requires a database)
# --------------------------------------------------------------------------- #
def _database_url() -> str | None:
    return (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip() or None


@pytest.mark.skipif(_database_url() is None, reason="requires PROGENITOR_DATABASE_URL")
def test_store_roundtrip_and_search() -> None:
    url = _database_url()
    assert url is not None
    base_id = kstore.new_base(name="pytest knowledge")["id"]
    with psycopg.connect(url) as conn:
        kstore.ensure_schema(conn)
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

            stats = kstore.base_document_stats(conn)
            assert stats[base_id]["documents"] == added
            assert "facts" in stats[base_id]["collections"]

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
            assert kstore.delete_documents_for_base(conn, base_id) >= 0
            assert kstore.base_document_stats(conn).get(base_id) is None


@pytest.mark.skipif(_database_url() is None, reason="requires PROGENITOR_DATABASE_URL")
def test_index_folder(tmp_path) -> None:
    url = _database_url()
    assert url is not None
    (tmp_path / "note.md").write_text("Project Lighthouse rollout notes.", encoding="utf-8")
    (tmp_path / "skip.bin").write_bytes(b"\x00\x01\x02")
    base_id = kstore.new_base(name="folder kb")["id"]
    source_id = kschema.generate_source_id()
    with psycopg.connect(url) as conn:
        kstore.ensure_schema(conn)
        try:
            result = kstore.index_folder(
                conn, base_id=base_id, source_id=source_id, path=str(tmp_path)
            )
            assert result["status"] == "indexed"
            assert result["document_count"] == 1  # the .bin file is skipped
            stats = kstore.base_document_stats(conn)
            assert stats[base_id]["documents"] == 1
        finally:
            kstore.delete_documents_for_source(conn, source_id)


def test_base_ids_are_slugs_with_collision_suffixes():
    payload = kschema.validate_knowledge_payload(
        {"bases": [{"name": "Engineering Notes"}, {"name": "Engineering Notes"}]}
    )
    assert [b["id"] for b in payload["bases"]] == ["engineering-notes", "engineering-notes-2"]
    reserved = kschema.validate_knowledge_payload(
        {"bases": [{"name": "Engineering Notes"}]}, reserved_ids=frozenset({"engineering-notes"})
    )
    assert reserved["bases"][0]["id"] == "engineering-notes-2"


def test_provided_non_slug_base_id_is_rejected():
    with pytest.raises(ValueError, match="lowercase kebab-case"):
        kschema.validate_knowledge_payload({"bases": [{"id": "Eng Notes", "name": "Eng"}]})
