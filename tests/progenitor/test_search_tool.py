"""``search`` tool: hybrid results, query-time freshness, object-agent scoping."""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from app.progenitor.knowledge import index as kindex
from app.progenitor.tools import search as search_module
from app.progenitor.tools.protocol import ToolContext
from app.progenitor.tools.search import SearchTool

DIMS = 1536
DB_URL = (os.getenv("PROGENITOR_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def _vector(axis: int) -> list[float]:
    vec = [0.0] * DIMS
    vec[axis] = 1.0
    return vec


def _page(title: str, body: str) -> str:
    return f"---\ntype: Concept\ntitle: {title}\n---\n{body}\n"


class FakeEmbedder:
    def __init__(self, axis: int) -> None:
        self.axis = axis

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_vector(self.axis) for _ in texts]


class FakeStateManager:
    def __init__(self, bases: list[str], pages: dict[tuple[str, str], str], profile: str) -> None:
        self.bases = bases
        self.pages = pages
        self.profile = profile

    async def get_knowledge_config(self):
        return {"bases": [{"id": base, "name": base.title()} for base in self.bases]}

    async def get_indexer_config(self):
        return {"profile": self.profile, "dimensions": DIMS}

    async def get_model_profiles_config(self):
        return {"embeddings": {"provider": "azure_openai", "deployment": "text-embedding-3-small"}}

    async def list_knowledge_pages(self):
        return [(b, p, {"content": c}) for (b, p), c in self.pages.items()]

    async def get_knowledge_index(self):
        return {"bases": {base: {"status": "indexed"} for base in self.bases}}


async def _run(tool: SearchTool, sm: FakeStateManager, **args):
    ctx = ToolContext(tool_registry=None, llm_client=None, state_manager=sm, tool_args=args)
    return await tool.execute(ctx)


async def test_unconfigured_profile_is_reported_without_touching_the_index(monkeypatch):
    monkeypatch.setenv("PROGENITOR_DATABASE_URL", "postgresql://unused")
    sm = FakeStateManager(["eng"], {}, profile="")
    result = await _run(SearchTool(), sm, query="anything")
    assert not result.success and "/config/indexer.json" in (result.error or "")


async def test_granted_agent_cannot_name_another_base(monkeypatch):
    monkeypatch.setenv("PROGENITOR_DATABASE_URL", "postgresql://unused")
    sm = FakeStateManager(["eng", "ops"], {}, profile="embeddings")
    result = await _run(SearchTool(allowed_bases=frozenset({"eng"})), sm, query="x", base="ops")
    assert not result.success and "granted: eng" in (result.error or "")


@pytest.mark.skipif(not DB_URL, reason="requires PROGENITOR_DATABASE_URL")
async def test_search_ranks_pages_scopes_bases_and_reports_pending(monkeypatch):
    run = uuid.uuid4().hex[:8]
    eng, ops = f"pytest-search-eng-{run}", f"pytest-search-ops-{run}"
    election = _page("Leader Election", "A candidate wins with a majority quorum.")
    replication = _page("Log Replication", "Followers append entries.")
    other = _page("Ops Majority", "Ops also mentions majority quorum.")
    with psycopg.connect(DB_URL) as conn:
        kindex.ensure_index_schema(conn, DIMS)
        for base, path, content, axis in (
            (eng, "election.md", election, 1),
            (eng, "replication.md", replication, 2),
            (ops, "majority.md", other, 1),
        ):
            front_matter, chunks = kindex.chunk_page(content)
            kindex.replace_page_chunks(
                conn,
                base,
                path,
                kindex.page_sha(content),
                front_matter["title"],
                chunks,
                [_vector(axis) for _ in chunks],
            )
    pages = {
        (eng, "election.md"): election,
        (eng, "replication.md"): replication.replace("append", "replicate"),  # edited since
        (eng, "new.md"): _page("New", "Not indexed yet."),
        (ops, "majority.md"): other,
    }
    sm = FakeStateManager([eng, ops], pages, profile="embeddings")
    monkeypatch.setattr(search_module, "embedder_for", lambda target: FakeEmbedder(1))
    try:
        everything = await _run(SearchTool(), sm, query="majority quorum")
        assert everything.success
        paths = [hit["path"] for hit in everything.data["results"]]
        assert set(paths[:2]) == {f"/knowledge/{eng}/election.md", f"/knowledge/{ops}/majority.md"}
        assert everything.data["index"][eng]["pending"] == ["new.md", "replication.md"]
        assert everything.data["index"][ops]["pending"] == []

        scoped = await _run(SearchTool(allowed_bases=frozenset({eng})), sm, query="majority quorum")
        assert scoped.success
        assert {hit["path"].split("/")[2] for hit in scoped.data["results"]} == {eng}
        assert set(scoped.data["index"]) == {eng}
    finally:
        with psycopg.connect(DB_URL) as conn:
            kindex.delete_page_chunks(conn, eng, ["election.md", "replication.md"])
            kindex.delete_page_chunks(conn, ops, ["majority.md"])
