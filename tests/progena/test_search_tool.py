"""``search`` tool: hybrid results, query-time freshness, object-agent scoping."""

from __future__ import annotations

import psycopg

from src.progena.knowledge import index as kindex
from src.progena.tools import search as search_module
from src.progena.tools.protocol import ToolContext
from src.progena.tools.search import SearchTool

DIMS = 1536


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


def _fake_embedder_factory(axis: int):
    async def resolve(_target):
        return FakeEmbedder(axis)

    return resolve


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
        return {"embeddings": {"provider_id": "foundry-1", "deployment": "text-embedding-3-small"}}

    async def list_knowledge_pages(self):
        return [(b, p, {"content": c}) for (b, p), c in self.pages.items()]

    async def get_knowledge_index(self):
        return {"bases": {base: {"status": "indexed"} for base in self.bases}}


async def _run(tool: SearchTool, sm: FakeStateManager, **args):
    ctx = ToolContext(tool_registry=None, llm_client=None, state_manager=sm, tool_args=args)
    return await tool.execute(ctx)


async def test_unconfigured_profile_is_reported_without_touching_the_index(monkeypatch):
    monkeypatch.setenv("PROGENA_DATABASE_URL", "postgresql://unused")
    sm = FakeStateManager(["eng"], {}, profile="")
    result = await _run(SearchTool(), sm, query="anything")
    assert not result.success and "/config/indexer.json" in (result.error or "")


async def test_granted_agent_cannot_name_another_base(monkeypatch):
    monkeypatch.setenv("PROGENA_DATABASE_URL", "postgresql://unused")
    sm = FakeStateManager(["eng", "ops"], {}, profile="embeddings")
    result = await _run(SearchTool(allowed_bases=frozenset({"eng"})), sm, query="x", base="ops")
    assert not result.success and "granted: eng" in (result.error or "")


async def test_search_ranks_pages_scopes_bases_and_reports_pending(monkeypatch, postgres_url):
    monkeypatch.setenv("PROGENA_DATABASE_URL", postgres_url)
    eng, ops = "eng", "ops"
    election = _page("Leader Election", "A candidate wins with a majority quorum.")
    replication = _page("Log Replication", "Followers append entries.")
    other = _page("Ops Majority", "Ops also mentions majority quorum.")
    retired = _page("Old Election", "A candidate wins with a majority quorum.").replace(
        "---\n", "---\nstatus: deprecated\n", 1
    )
    with psycopg.connect(postgres_url) as conn:
        kindex.ensure_index_schema(conn, DIMS)
        for base, path, content, axis in (
            (eng, "election.md", election, 1),
            (eng, "replication.md", replication, 2),
            (eng, "old-election.md", retired, 1),
            (ops, "majority.md", other, 1),
        ):
            front_matter, chunks = kindex.chunk_page(content)
            kindex.replace_page_chunks(
                conn,
                base,
                path,
                kindex.page_sha(content),
                front_matter,
                chunks,
                [_vector(axis) for _ in chunks],
            )
    pages = {
        (eng, "election.md"): election,
        (eng, "replication.md"): replication.replace("append", "replicate"),  # edited since
        (eng, "new.md"): _page("New", "Not indexed yet."),
        (eng, "old-election.md"): retired,
        (ops, "majority.md"): other,
    }
    sm = FakeStateManager([eng, ops], pages, profile="embeddings")
    monkeypatch.setattr(search_module, "embedder_for", _fake_embedder_factory(1))
    everything = await _run(SearchTool(), sm, query="majority quorum")
    assert everything.success
    paths = [hit["path"] for hit in everything.data["results"]]
    assert set(paths[:2]) == {f"/knowledge/{eng}/election.md", f"/knowledge/{ops}/majority.md"}
    # A deprecated page matching just as well stays findable but ranks after current pages.
    assert paths[2] == f"/knowledge/{eng}/old-election.md"
    assert everything.data["results"][2]["deprecated"] is True
    assert "deprecated" not in everything.data["results"][0]
    assert everything.data["index"][eng]["pending"] == ["new.md", "replication.md"]
    assert everything.data["index"][ops]["pending"] == []
    # Below the similarity cut-off and no keyword match: left out.
    assert f"/knowledge/{eng}/replication.md" not in paths
    election_hit = everything.data["results"][paths.index(f"/knowledge/{eng}/election.md")]
    assert election_hit["similarity"] == 1.0 and election_hit["keyword_match"] is True

    # A keyword match is kept even when its meaning is unrelated.
    monkeypatch.setattr(search_module, "embedder_for", _fake_embedder_factory(7))
    keyword_only = await _run(SearchTool(), sm, query="followers")
    assert [hit["path"] for hit in keyword_only.data["results"]] == [
        f"/knowledge/{eng}/replication.md"
    ]
    nothing = await _run(SearchTool(), sm, query="pizza recipe")
    assert nothing.success and nothing.data["results"] == []
    monkeypatch.setattr(search_module, "embedder_for", _fake_embedder_factory(1))

    scoped = await _run(SearchTool(allowed_bases=frozenset({eng})), sm, query="majority quorum")
    assert scoped.success
    assert {hit["path"].split("/")[2] for hit in scoped.data["results"]} == {eng}
    assert set(scoped.data["index"]) == {eng}
