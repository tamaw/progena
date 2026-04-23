"""
Tests for Phase 3: Backend configuration and persisted /memories routing.
"""

from unittest.mock import MagicMock

from app.progenitor.agent.backends import (
    ReadOnlyFilesystemBackend,
    create_progenitor_backend,
)


def _file_info_path(info: object) -> str:
    if isinstance(info, str):
        return info
    path = info.get("path") if isinstance(info, dict) else getattr(info, "path", None)
    assert isinstance(path, str)
    return path


def _read_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    file_data = getattr(result, "file_data", None)
    if isinstance(file_data, dict):
        file_content = file_data.get("content")
        if isinstance(file_content, str):
            return file_content
    text = getattr(result, "text", None)
    assert isinstance(text, str)
    return text


class TestBackendFactory:
    """Tests for the backend factory."""

    def test_returns_callable(self):
        """Factory returns a callable."""
        factory = create_progenitor_backend()
        assert callable(factory)

    def test_factory_creates_composite_backend(self):
        """Factory callable creates a CompositeBackend when called with runtime."""
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

        factory = create_progenitor_backend(project_root=".")
        runtime = MagicMock()

        backend = factory(runtime)
        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, StateBackend)
        assert isinstance(backend.routes["/memories/"], FilesystemBackend)

    def test_factory_uses_project_root(self):
        """project_root is accepted (even though core-only doesn't use it)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            from deepagents.backends import FilesystemBackend

            root = Path(tmpdir)
            for rel in ("app", "data", "data/prompts", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            factory = create_progenitor_backend(project_root=tmpdir)
            runtime = MagicMock()

            backend = factory(runtime)
            assert backend is not None
            assert "/memories/" in backend.routes
            assert "/self/" in backend.routes
            assert "/data/" in backend.routes
            assert "/logs/" in backend.routes
            assert "/prompts/" in backend.routes
            assert isinstance(backend.routes["/memories/"], FilesystemBackend)
            assert isinstance(backend.routes["/self/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/data/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/logs/"], ReadOnlyFilesystemBackend)
            assert isinstance(backend.routes["/prompts/"], ReadOnlyFilesystemBackend)
            assert (root / "data" / "memories").is_dir()

    def test_factory_defaults_to_cwd(self):
        """Factory defaults to cwd when no project_root specified."""

        factory = create_progenitor_backend()
        # Just verify it returns without error
        assert factory is not None

    def test_memories_route_persists_written_files(self):
        """Writes to /memories are persisted under data/memories on disk."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for rel in ("app", "data", "data/prompts", "logs"):
                (root / rel).mkdir(parents=True, exist_ok=True)

            factory = create_progenitor_backend(project_root=tmpdir)
            backend = factory(MagicMock())

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

            listing = backend.ls("/memories/patterns/")
            assert listing.error is None
            assert "/memories/patterns/testing.md" in {
                _file_info_path(info) for info in listing.entries
            }
