from deepagents.backends import CompositeBackend

from app.progenitor.agent.knowledge_backend import KnowledgePageBackend

PAGE = "---\ntype: Concept\ntitle: A\ndescription: First page\n---\nRetry budget is three.\n"


class FakeStateManager:
    def __init__(self):
        self.pages = {}
        self.instructions = ""

    async def get_knowledge_config(self):
        return {
            "bases": [
                {"id": "eng", "name": "Engineering", "instructions": self.instructions},
                {"id": "ops", "name": "Ops"},
            ]
        }

    async def get_knowledge_page(self, base, path):
        return self.pages.get((base, path))

    async def save_knowledge_page(self, base, path, content):
        self.pages[(base, path)] = {"content": content, "updated_at": "now"}

    async def list_knowledge_pages(self):
        return [(base, path, payload) for (base, path), payload in self.pages.items()]


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


async def test_log_is_append_only_and_agents_cannot_delete():
    backend = KnowledgePageBackend(FakeStateManager())
    assert (await backend.awrite("/eng/log.md", "one\n")).error is None
    assert "append-only" in (await backend.awrite("/eng/log.md", "changed\n")).error
    assert (await backend.awrite("/eng/log.md", "one\ntwo\n")).error is None
    assert "only allowed at the base root" in (await backend.awrite("/eng/x/log.md", "a")).error
    await backend.awrite("/eng/a.md", PAGE)
    assert "cannot be deleted" in (await backend.adelete("/eng/a.md")).error


async def test_soft_deleted_page_is_hidden_until_rewritten():
    manager = FakeStateManager()
    backend = KnowledgePageBackend(manager)
    await backend.awrite("/eng/concepts/a.md", PAGE)
    manager.pages[("eng", "concepts/a.md")]["deleted_at"] = "now"
    assert (await backend.aread("/eng/concepts/a.md")).error
    assert [e["path"] for e in (await backend.als("/eng/")).entries] == ["/eng/index.md"]
    assert (await backend.awrite("/eng/concepts/a.md", PAGE)).error is None
    assert (await backend.aread("/eng/concepts/a.md")).error is None


async def test_base_index_shows_catalog_instructions_and_brief_file_is_not_a_page():
    manager = FakeStateManager()
    manager.instructions = "Keep one page per decision."
    backend = KnowledgePageBackend(manager)
    index = (await backend.aread("/eng/index.md")).file_data["content"]
    assert "## Instructions\n\nKeep one page per decision." in index
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
