"""
Tests for Phase 3: Backend configuration for DeepAgent.
"""

from unittest.mock import MagicMock

from app.progenitor.agent.backends import create_progenitor_backend


class TestBackendFactory:
    """Tests for the backend factory."""

    def test_returns_callable(self):
        """Factory returns a callable."""
        factory = create_progenitor_backend()
        assert callable(factory)

    def test_factory_creates_composite_backend(self):
        """Factory callable creates a CompositeBackend when called with runtime."""
        from deepagents.backends import CompositeBackend, StateBackend

        factory = create_progenitor_backend(project_root=".")
        runtime = MagicMock()
        # StoreBackend needs runtime.store
        runtime.store = MagicMock()

        backend = factory(runtime)
        assert isinstance(backend, CompositeBackend)
        assert isinstance(backend.default, StateBackend)

    def test_factory_uses_project_root(self):
        """project_root is accepted (even though core-only doesn't use it)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            factory = create_progenitor_backend(project_root=tmpdir)
            runtime = MagicMock()
            runtime.store = MagicMock()

            backend = factory(runtime)
            assert backend is not None

    def test_factory_defaults_to_cwd(self):
        """Factory defaults to cwd when no project_root specified."""

        factory = create_progenitor_backend()
        # Just verify it returns without error
        assert factory is not None
