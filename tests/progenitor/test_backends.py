"""
Tests for Phase 3: Backend configuration and persisted /memories routing.
"""

from pathlib import Path

from app.progenitor.agent.backends import ReadOnlyFilesystemBackend, create_progenitor_backend


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

    def test_returns_composite_backend(self, monkeypatch):
        """Factory returns a backend instance, not a runtime callback."""
        from deepagents.backends import CompositeBackend, FilesystemBackend

        runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend(project_root=".")

        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, runtime_free_cls)
        assert isinstance(backend.routes["/memories/"], FilesystemBackend)

    def test_factory_uses_project_root(self, monkeypatch):
        """project_root is accepted for scoped filesystem mounts."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from deepagents.backends import FilesystemBackend

            runtime_free_cls = self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data", "data/prompts", "data/schemas", "logs", "repos"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            backend = create_progenitor_backend(project_root=tmpdir, worktree_dir=root / "repos")
            assert backend is not None
            assert isinstance(backend.default, runtime_free_cls)
            assert "/memories/" in backend.routes
            assert "/self/" in backend.routes
            assert "/repos/" in backend.routes
            assert "/data/" in backend.routes
            assert "/logs/" in backend.routes
            assert "/prompts/" in backend.routes
            assert "/schemas/" in backend.routes
            assert isinstance(backend.routes["/memories/"], FilesystemBackend)
            assert isinstance(backend.routes["/self/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/repos/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/data/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/logs/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/prompts/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/schemas/"], ReadOnlyFilesystemBackend)
            assert (root / "data" / "memories").is_dir()

    def test_state_manager_mounts_state_and_inbox_routes(self, monkeypatch):
        """A state_manager adds the /state/, /state/tasks/, and /inbox/ virtual mounts."""
        from unittest.mock import MagicMock

        from app.progenitor.agent.state_backend import (
            InboxDirectoryBackend,
            KanbanTaskDirectoryBackend,
            StateDocumentBackend,
        )

        self._patch_runtime_free_state_backend(monkeypatch)

        # The state-file factories bind StateManager getters/setters at
        # construction time, so the stub needs arbitrary attribute access.
        backend = create_progenitor_backend(project_root=".", state_manager=MagicMock())
        assert isinstance(backend.routes["/state/"], StateDocumentBackend)
        assert isinstance(backend.routes["/state/tasks/"], KanbanTaskDirectoryBackend)
        assert isinstance(backend.routes["/inbox/"], InboxDirectoryBackend)

    def test_factory_defaults_to_cwd(self, monkeypatch):
        """Factory defaults to cwd when no project_root specified."""
        self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend()
        assert backend is not None

    def test_memories_write_and_schemas_read_round_trip(self, monkeypatch):
        """/memories/ (read-write) and /schemas/ (read-only) both work end to end."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data", "data/prompts", "data/schemas", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            schema = {"type": "object", "properties": {"id": {"type": "string"}}}
            (root / "data" / "schemas" / "widget.schema.v1.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )

            backend = create_progenitor_backend(project_root=tmpdir)

            content = "## Testing\n- Keep tests focused.\n"
            uploads = backend.upload_files(
                [("/memories/patterns/testing.md", content.encode("utf-8"))]
            )
            persisted_path = root / "data" / "memories" / "patterns" / "testing.md"
            downloads = backend.download_files(
                ["/memories/patterns/testing.md", "/schemas/widget.schema.v1.json"]
            )

            assert len(uploads) == 1
            assert uploads[0].error is None
            assert persisted_path.read_text(encoding="utf-8") == content
            assert downloads[0].error is None
            assert downloads[0].content.decode("utf-8") == content
            assert downloads[1].error is None
            assert json.loads(downloads[1].content.decode("utf-8")) == schema
