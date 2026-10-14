from deepagents.backends import CompositeBackend

from app.progenitor.agent.knowledge_backend import KnowledgePageBackend

PAGE = "---\ntype: Concept\ntitle: A\ndescription: First page\n---\nRetry budget is three.\n"


class FakeStateManager:
    def __init__(self):
        self.pages = {}
        self.index = {"embedding": None, "bases": {}}

    async def get_knowledge_config(self):
        return {
            "bases": [
                {"id": "eng", "name": "Engineering"},
                {"id": "ops", "name": "Ops"},
            ]
        }

    async def get_knowledge_page(self, base, path):
        return self.pages.get((base, path))

    async def save_knowledge_page(self, base, path, content):
        self.pages[(base, path)] = {"content": content, "updated_at": "now"}

    async def list_knowledge_pages(self):
        return [(base, path, payload) for (base, path), payload in self.pages.items()]

    async def get_knowledge_index(self):
        return self.index


async def test_writes_validate_base_path_and_front_matter():
    manager = FakeStateManager()
    backend = KnowledgePageBackend(manager)
    assert "Unknown knowledge base 'missing'" in (await backend.awrite("/missing/a.md", PAGE)).error
    assert "non-empty 'type'" in (await backend.awrite("/eng/concepts/a.md", "# no type")).error
    assert "kebab-case" in (await backend.awrite("/eng/Concepts/A.md", PAGE)).error
    assert (await backend.awrite("/eng/concepts/a.md", PAGE)).error is None
    stored = manager.pages[("eng", "concepts/a.md")]["content"]
    assert "generated:" in stored and "at:" in stored


async def test_generated_index_lists_pages_and_cannot_be_written():
    manager = FakeStateManager()
    backend = KnowledgePageBackend(manager)
    await backend.awrite("/eng/concepts/a.md", PAGE)
    await backend.awrite("/eng/top.md", PAGE.replace("title: A", "title: Top"))
    assert "generated" in (await backend.awrite("/eng/index.md", "x")).error
    root = (await backend.aread("/eng/index.md")).file_data["content"]
    assert "- [Top](top.md) — First page" in root
    assert "- [concepts/](concepts/index.md)" in root
    nested = (await backend.aread("/eng/concepts/index.md")).file_data["content"]
    assert "- [A](a.md) — First page" in nested
    assert "[Engineering](eng/index.md)" in (await backend.aread("/index.md")).file_data["content"]


async def test_indexer_status_is_a_read_only_root_file_and_shown_in_indexes():
    manager = FakeStateManager()
    manager.index = {
        "embedding": {"profile": "embeddings", "deployment": "d", "dimensions": 1536},
        "bases": {"eng": {"status": "indexing", "pages": 7, "indexed_pages": 3}},
    }
    backend = KnowledgePageBackend(manager)
    assert "/indexer.json" in [e["path"] for e in (await backend.als("/")).entries]
    status = (await backend.aread("/indexer.json")).file_data["content"]
    assert '"indexed_pages": 3' in status
    assert "/config/indexer.json" in (await backend.awrite("/indexer.json", "{}")).error
    root = (await backend.aread("/index.md")).file_data["content"]
    assert "[Engineering](eng/index.md) · search: indexing 3/7" in root
    assert "[Ops](ops/index.md) · search: not indexed yet" in root
    base = (await backend.aread("/eng/index.md")).file_data["content"]
    assert "# Engineering\n\nSearch index: indexing 3/7\n" in base


async def test_log_is_append_only_and_agents_cannot_delete():
    backend = KnowledgePageBackend(FakeStateManager())
    assert (await backend.awrite("/eng/log.md", "one\n")).error is None
    assert "append-only" in (await backend.awrite("/eng/log.md", "changed\n")).error
    assert (await backend.awrite("/eng/log.md", "one\ntwo\n")).error is None
    assert "only allowed at the base root" in (await backend.awrite("/eng/x/log.md", "a")).error
    await backend.awrite("/eng/a.md", PAGE)
    assert "cannot be deleted" in (await backend.adelete("/eng/a.md")).error


async def test_invalid_legacy_rows_are_neither_listed_nor_readable():
    manager = FakeStateManager()
    backend = KnowledgePageBackend(manager)
    assert "kebab-case" in (await backend.awrite("/eng/INSTRUCTIONS.md", "brief")).error
    # A legacy INSTRUCTIONS.md row is neither listed nor readable.
    manager.pages[("eng", "INSTRUCTIONS.md")] = {"content": "old brief"}
    assert "/eng/INSTRUCTIONS.md" not in [e["path"] for e in (await backend.als("/eng/")).entries]
    assert (await backend.aread("/eng/INSTRUCTIONS.md")).error


async def test_composite_route_lists_greps_and_globs():
    manager = FakeStateManager()
    composite = CompositeBackend(
        default=object(), routes={"/knowledge/": KnowledgePageBackend(manager)}
    )
    assert (await composite.awrite("/knowledge/eng/concepts/a.md", PAGE)).error is None
    assert manager.pages[("eng", "concepts/a.md")]
    listed = [e["path"] for e in (await composite.als("/knowledge/eng/")).entries]
    assert listed == ["/knowledge/eng/concepts/", "/knowledge/eng/index.md"]
    matches = (await composite.agrep("Retry budget", "/knowledge/")).matches
    assert [m["path"] for m in matches] == ["/knowledge/eng/concepts/a.md"]
    globbed = [m["path"] for m in (await composite.aglob("**/*.md", "/knowledge/eng/")).matches]
    assert "/knowledge/eng/concepts/a.md" in globbed
