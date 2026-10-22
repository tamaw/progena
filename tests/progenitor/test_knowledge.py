"""Tests for the knowledge catalog schema (``/config/knowledge.json``)."""

from __future__ import annotations

import pytest

from src.progenitor.knowledge import schema as kschema


def test_validate_payload_normalizes_and_drops_derived_and_retired_fields() -> None:
    result = kschema.validate_knowledge_payload(
        {
            "bases": [
                {
                    "name": "Notes",
                    "tags": ["a", "a", " b "],
                    "page_count": 9,  # derived snapshot field
                    "index_status": "indexed",  # derived snapshot field
                    "status": "connected",  # retired catalog field
                    "sources": [{"path": "/tmp/docs"}],  # retired catalog field
                }
            ]
        }
    )
    base = result["bases"][0]
    assert base["id"] == "notes"
    assert base["created_at"] and base["updated_at"]
    assert base["tags"] == ["a", "b"]
    assert not {"page_count", "index_status", "status", "sources"} & base.keys()


def test_validate_payload_rejects_bad_values() -> None:
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload({"bases": [{"description": "no name"}]})
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload(["not", "an", "object"])
    with pytest.raises(ValueError):
        kschema.validate_knowledge_document("{not json}")


def test_validate_payload_rejects_duplicate_ids() -> None:
    with pytest.raises(ValueError):
        kschema.validate_knowledge_payload(
            {"bases": [{"id": "dup", "name": "A"}, {"id": "dup", "name": "B"}]}
        )


def test_resolve_base_ref_by_id_then_case_insensitive_name() -> None:
    bases = [{"id": "my-kb", "name": "My KB"}]
    assert kschema.find_base(bases, "my-kb") is bases[0]
    assert kschema.find_base(bases, "missing") is None
    assert kschema.resolve_base_ref(bases, "my-kb") == "my-kb"
    assert kschema.resolve_base_ref(bases, "my kb") == "my-kb"
    assert kschema.resolve_base_ref(bases, "nope") is None


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
