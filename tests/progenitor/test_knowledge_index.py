"""Pure tests for knowledge search indexing: chunking, freshness, settings, status text."""

from __future__ import annotations

import pytest

from app.progenitor.knowledge.embedding import embeddings_base_url
from app.progenitor.knowledge.index import (
    MAX_CHUNK_CHARS,
    chunk_page,
    embedding_text,
    page_sha,
    pending_paths,
)
from app.progenitor.knowledge.indexer_config import (
    NOT_CONFIGURED_MESSAGE,
    describe_index_status,
    resolve_embedding_target,
    validate_indexer_payload,
)

PAGE = """---
type: Concept
title: Leader Election
description: How Raft picks a leader
---
Intro paragraph.

## Votes

A candidate needs a majority.

```
## not a heading inside code
```

### Terms

Terms are logical clocks.
"""


def test_chunk_page_splits_on_headings_but_not_inside_code_fences():
    front_matter, chunks = chunk_page(PAGE)
    assert front_matter["title"] == "Leader Election"
    assert [chunk.heading for chunk in chunks] == ["", "Votes", "Terms"]
    assert "## not a heading inside code" in chunks[1].text
    assert "type: Concept" not in "".join(chunk.text for chunk in chunks)
    text = embedding_text(front_matter, chunks[1])
    assert text.startswith("Leader Election — How Raft picks a leader\nVotes\n")


def test_chunk_page_caps_chunk_size_and_keeps_empty_pages_searchable():
    paragraphs = "\n\n".join("word " * 100 for _ in range(10))  # ~5,000 chars
    _, chunks = chunk_page(f"---\ntype: Concept\n---\n## Long\n\n{paragraphs}\n")
    assert len(chunks) > 1
    assert all(len(chunk.text) <= MAX_CHUNK_CHARS for chunk in chunks)
    _, empty = chunk_page("---\ntype: Concept\ntitle: Empty\ndescription: Nothing yet\n---\n")
    assert [chunk.text for chunk in empty] == ["Empty — Nothing yet"]


def test_chunk_page_keeps_headings_of_sections_without_text():
    _, chunks = chunk_page("---\ntype: Concept\n---\n# Raft\n\n## Elections\n\nVotes.\n")
    assert [(chunk.heading, chunk.text) for chunk in chunks] == [("Raft › Elections", "Votes.")]
    _, only_headings = chunk_page("---\ntype: Concept\n---\n# Raft\n## Elections\n")
    assert [chunk.heading for chunk in only_headings] == ["Raft › Elections"]


def test_pending_paths_lists_unindexed_or_changed_concept_pages_including_deprecated():
    retired = "---\ntype: Concept\nstatus: deprecated\n---\nOld.\n"
    live = {
        "a.md": "A",
        "b.md": "B2",
        "c.md": "C",
        "d.md": retired,
        "log.md": "log",
        "index.md": "x",
    }
    indexed = {"a.md": page_sha("A"), "b.md": page_sha("B1")}
    assert pending_paths(live, indexed) == ["b.md", "c.md", "d.md"]


def test_indexer_settings_validate_profile_and_dimensions():
    assert validate_indexer_payload({"profile": " embeddings "}, profile_names={"embeddings"}) == {
        "profile": "embeddings",
        "dimensions": 1536,
        "min_similarity": 0.25,
    }
    with pytest.raises(ValueError, match="not in /config/profile.json"):
        validate_indexer_payload({"profile": "missing"}, profile_names={"general"})
    with pytest.raises(ValueError):
        validate_indexer_payload({"profile": "", "dimensions": 4096})
    with pytest.raises(ValueError):
        validate_indexer_payload({"profile": "", "model": "x"})
    with pytest.raises(ValueError):
        validate_indexer_payload({"profile": "", "min_similarity": 1.5})


def test_resolve_embedding_target_needs_a_profile_with_a_deployment():
    profiles = {"embeddings": {"provider": "azure_openai", "deployment": "text-embedding-3-small"}}
    target = resolve_embedding_target({"profile": "embeddings", "dimensions": 512}, profiles)
    assert target.as_dict() == {
        "profile": "embeddings",
        "deployment": "text-embedding-3-small",
        "dimensions": 512,
    }
    for settings in ({"profile": ""}, {"profile": "gone"}, None):
        with pytest.raises(ValueError, match="/config/indexer.json"):
            resolve_embedding_target(settings, profiles)
    assert "/config/profile.json" in NOT_CONFIGURED_MESSAGE


def test_embeddings_base_url_strips_the_responses_endpoint():
    assert (
        embeddings_base_url(
            "https://r.cognitiveservices.azure.com/openai/v1/responses?api-version=preview"
        )
        == "https://r.cognitiveservices.azure.com/openai/v1"
    )
    assert embeddings_base_url("https://r.openai.azure.com/openai/v1/") == (
        "https://r.openai.azure.com/openai/v1"
    )


def test_describe_index_status():
    assert describe_index_status(None) == "not indexed yet"
    assert describe_index_status({"status": "indexing", "pages": 7, "indexed_pages": 3}) == (
        "indexing 3/7"
    )
    assert describe_index_status(
        {"status": "indexed", "pages": 1, "indexed_at": "2026-10-13T09:41:12+00:00"}
    ) == ("indexed 1 page (2026-10-13 09:41 UTC)")
    assert describe_index_status({"status": "failed", "error": "boom"}) == "failed: boom"
