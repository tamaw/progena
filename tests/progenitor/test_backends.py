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
            for rel in ("app", "data", "data/prompts", "logs", "repos"):
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
            assert isinstance(backend.routes["/memories/"], FilesystemBackend)
            assert isinstance(backend.routes["/self/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/repos/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/data/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/logs/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/prompts/"], ReadOnlyFilesystemBackend)
            assert (root / "data" / "memories").is_dir()

    def test_factory_defaults_to_cwd(self, monkeypatch):
        """Factory defaults to cwd when no project_root specified."""
        self._patch_runtime_free_state_backend(monkeypatch)
        backend = create_progenitor_backend()
        assert backend is not None

    def test_memories_route_persists_written_files(self, monkeypatch):
        """Writes to /memories are persisted under data/memories on disk."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            self._patch_runtime_free_state_backend(monkeypatch)
            root = Path(tmpdir)
            for rel in ("app", "data", "data/prompts", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            backend = create_progenitor_backend(project_root=tmpdir)

            content = "## Testing\n- Keep tests focused.\n"
            uploads = backend.upload_files(
                [("/memories/patterns/testing.md", content.encode("utf-8"))]
            )
            persisted_path = root / "data" / "memories" / "patterns" / "testing.md"
            downloads = backend.download_files(["/memories/patterns/testing.md"])

            assert len(uploads) == 1
            assert uploads[0].error is None
            assert persisted_path.read_text(encoding="utf-8") == content
            assert downloads[0].error is None
            assert downloads[0].content.decode("utf-8") == content
