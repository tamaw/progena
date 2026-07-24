"""
Tests for Phase 3: Backend configuration and Postgres-backed content routing.
"""

from pathlib import Path
from unittest.mock import MagicMock

from app.progenitor.agent.backends import ReadOnlyBackend, create_progenitor_backend


class FakeContentStateManager(MagicMock):
    """Async state_manager stub backing get/save/delete/list_content_document.

    Subclasses MagicMock so the unrelated /config/ state-file factories (which
    bind arbitrary StateManager getters/setters at construction time, e.g.
    ``get_mcp_config``) keep working without needing every method stubbed.
    """

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


class TestBackendFactory:
    """Tests for the backend factory."""

    @staticmethod
    def _patch_runtime_free_state_backend(monkeypatch):
        class RuntimeFreeStateBackend:
            def __init__(self):
                self.mode = "runtime-free"

        monkeypatch.setattr(
            "app.progenitor.agent.backends.StateBackend",
            RuntimeFreeStateBackend,
        )
        return RuntimeFreeStateBackend

    def test_returns_composite_backend_without_state_manager(self, monkeypatch):
        """Without a state_manager, only the disk-backed routes are mounted."""
        from deepagents.backends import CompositeBackend

        runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend(project_root=".")

        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, runtime_free_cls)
        assert "/memories/" not in backend.routes
        assert "/prompts/" not in backend.routes
        assert "/skills/" not in backend.routes
        assert "/config/" not in backend.routes

    def test_factory_uses_project_root(self, monkeypatch):
        """project_root is accepted for scoped filesystem mounts."""
        import tempfile

        from deepagents.backends import FilesystemBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data/schemas", "logs", "repos"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            state_manager = FakeContentStateManager()
            backend = create_progenitor_backend(
                project_root=tmpdir, worktree_dir=root / "repos", state_manager=state_manager
            )
            assert backend is not None
            assert isinstance(backend.default, runtime_free_cls)
            assert "/memories/" in backend.routes
            assert "/prompts/" in backend.routes
            assert "/skills/" in backend.routes
            assert "/self/" in backend.routes
            assert "/repos/" in backend.routes
            assert "/logs/" in backend.routes
            assert "/schemas/" in backend.routes
            assert isinstance(backend.routes["/self/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/repos/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/logs/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/schemas/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/schemas/"]._backend, FilesystemBackend)
            assert isinstance(backend.routes["/prompts/"], ReadOnlyBackend)
            assert isinstance(backend.routes["/skills/"], ReadOnlyBackend)

    def test_state_manager_mounts_config_and_inbox_routes(self, monkeypatch):
        """A state_manager adds the /config/, /config/tasks/, /config/workflows/, and /inbox/ mounts."""
        from unittest.mock import MagicMock

        from app.progenitor.agent.state_backend import (
            InboxDirectoryBackend,
            KanbanTaskDirectoryBackend,
            StateDocumentBackend,
            WorkflowDirectoryBackend,
        )

        self._patch_runtime_free_state_backend(monkeypatch)

        # The state-file factories bind StateManager getters/setters at
        # construction time, so the stub needs arbitrary attribute access.
        backend = create_progenitor_backend(project_root=".", state_manager=MagicMock())
        assert isinstance(backend.routes["/config/"], StateDocumentBackend)
        assert isinstance(backend.routes["/config/tasks/"], KanbanTaskDirectoryBackend)
        assert isinstance(backend.routes["/config/workflows/"], WorkflowDirectoryBackend)
        assert isinstance(backend.routes["/inbox/"], InboxDirectoryBackend)

    def test_factory_defaults_to_cwd(self, monkeypatch):
        """Factory defaults to cwd when no project_root specified."""
        self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend()
        assert backend is not None

    async def test_memories_write_and_schemas_read_round_trip(self, monkeypatch):
        """/memories/ (Postgres, read-write) and /schemas/ (disk, read-only) both work end to end."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data/schemas", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            schema = {"type": "object", "properties": {"id": {"type": "string"}}}
            (root / "data" / "schemas" / "widget.schema.v1.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )

            state_manager = FakeContentStateManager()
            backend = create_progenitor_backend(project_root=tmpdir, state_manager=state_manager)

            content = "## Testing\n- Keep tests focused.\n"
            written = await backend.awrite("/memories/patterns/testing.md", content)
            memory_read = await backend.aread("/memories/patterns/testing.md")
            schema_read = await backend.aread("/schemas/widget.schema.v1.json")

            assert written.error is None
            assert state_manager._docs["memory"]["patterns/testing.md"] == content
            assert memory_read.error is None
            assert memory_read.file_data["content"] == content
            assert schema_read.error is None
            assert json.loads(schema_read.file_data["content"]) == schema

    async def test_prompts_and_skills_are_read_only(self, monkeypatch):
        """/prompts/ and /skills/ read Postgres content and are wrapped read-only."""
        self._patch_runtime_free_state_backend(monkeypatch)
        state_manager = FakeContentStateManager()
        state_manager._docs["prompt"]["progenitor/genesis.md"] = "# Genesis\n"
        state_manager._docs["skill"]["progenitor/example/SKILL.md"] = "---\nname: example\n---\n"

        backend = create_progenitor_backend(project_root=".", state_manager=state_manager)

        prompt_read = await backend.aread("/prompts/progenitor/genesis.md")
        skill_read = await backend.aread("/skills/progenitor/example/SKILL.md")
        assert prompt_read.error is None
        assert prompt_read.file_data["content"] == "# Genesis\n"
        assert skill_read.error is None

        assert isinstance(backend.routes["/prompts/"], ReadOnlyBackend)
        assert isinstance(backend.routes["/skills/"], ReadOnlyBackend)
