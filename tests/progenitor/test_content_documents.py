"""Regression tests for `ContentDocumentDirectoryBackend.als` skill discovery.

`SkillsMiddleware` only treats `is_dir`-truthy `als()` entries as skill
directories (`deepagents/middleware/skills.py:_alist_skills_with_errors`).
`ContentDocumentDirectoryBackend.als` previously returned every content
document row flat, all with `is_dir=False`, so skill discovery silently
returned zero skills even though the documents existed and were readable by
exact path. See `src/progenitor/agent/filesystem/:ContentDocumentDirectoryBackend.als`.
"""

from unittest.mock import MagicMock

from deepagents.middleware.skills import _alist_skills_with_errors

from src.progenitor.agent.filesystem.content import ContentDocumentDirectoryBackend


class FakeContentStateManager(MagicMock):
    """Async state_manager stub backing get/save/delete/list_content_document."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._docs: dict[str, dict[str, str]] = {"prompt": {}, "skill": {}, "memory": {}}

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())


class TestContentDocumentDirectoryBackendAls:
    """`als` must synthesize one level of directories, not a flat file list."""

    async def test_ls_root_returns_one_level_of_directories(self):
        state_manager = FakeContentStateManager()
        state_manager._docs["skill"]["progenitor/checking-status/SKILL.md"] = (
            "---\nname: checking-status\n---\n"
        )
        state_manager._docs["skill"]["progenitor/managing-mcp/SKILL.md"] = (
            "---\nname: managing-mcp\n---\n"
        )
        backend = ContentDocumentDirectoryBackend(state_manager, "skill")

        result = await backend.als("/")

        assert [(e["path"], e["is_dir"]) for e in result.entries] == [("/progenitor", True)]

    async def test_ls_one_level_down_returns_skill_directories(self):
        state_manager = FakeContentStateManager()
        state_manager._docs["skill"]["progenitor/checking-status/SKILL.md"] = (
            "---\nname: checking-status\n---\n"
        )
        state_manager._docs["skill"]["progenitor/managing-mcp/SKILL.md"] = (
            "---\nname: managing-mcp\n---\n"
        )
        backend = ContentDocumentDirectoryBackend(state_manager, "skill")

        result = await backend.als("/progenitor")

        assert sorted((e["path"], e["is_dir"]) for e in result.entries) == [
            ("/progenitor/checking-status", True),
            ("/progenitor/managing-mcp", True),
        ]

    async def test_skills_middleware_discovers_seeded_skills(self):
        """End-to-end proof: the real SkillsMiddleware scan must find the seeded skills.

        This is the regression the bug produced: `als` returning every entry
        flat with `is_dir=False` made `_alist_skills_with_errors` see zero
        skill directories, so `skills discovered: []` even though both
        SKILL.md files existed and were individually readable.
        """
        state_manager = FakeContentStateManager()
        state_manager._docs["skill"]["progenitor/checking-status/SKILL.md"] = (
            "---\nname: checking-status\ndescription: Check status.\n---\nBody.\n"
        )
        state_manager._docs["skill"]["progenitor/managing-mcp/SKILL.md"] = (
            "---\nname: managing-mcp\ndescription: Manage MCP servers.\n---\nBody.\n"
        )
        backend = ContentDocumentDirectoryBackend(state_manager, "skill")

        skills, error = await _alist_skills_with_errors(backend, "/progenitor")

        assert error is None
        assert sorted(skill["name"] for skill in skills) == ["checking-status", "managing-mcp"]
