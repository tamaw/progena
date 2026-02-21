"""
Tests for Phase 6: Memory system wiring.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestMemoryWiring:
    """Test that memory is wired into the DeepAgent factory."""

    def test_factory_passes_explicit_memory(self):
        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create:
            mock_create.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.deep_agent import create_progenitor_agent

                create_progenitor_agent(
                    memory_paths=["AGENTS.md", "docs/notes.md"],
                )

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["memory"] == ["AGENTS.md", "docs/notes.md"]

    def test_factory_auto_discovers_agents_md(self):
        """When no memory_paths given, auto-discovers AGENTS.md."""
        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create:
            mock_create.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.deep_agent import create_progenitor_agent

                # Project root has AGENTS.md
                create_progenitor_agent(project_root=".")

                call_kwargs = mock_create.call_args.kwargs
                memory = call_kwargs.get("memory")
                assert memory is not None
                assert any("AGENTS.md" in p for p in memory)

    def test_factory_no_memory_when_no_agents_md(self):
        """When AGENTS.md doesn't exist, memory is None."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            from app.progenitor.agent.deep_agent import create_progenitor_agent

            create_progenitor_agent(project_root=tmpdir)

            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs.get("memory") is None


class TestAgentsMdContent:
    """Verify AGENTS.md is suitable as a memory source."""

    def test_agents_md_exists(self):
        assert Path("AGENTS.md").is_file()

    def test_agents_md_has_content(self):
        content = Path("AGENTS.md").read_text(encoding="utf-8")
        assert len(content) > 100, "AGENTS.md should have substantial content"

    def test_agents_md_has_key_sections(self):
        content = Path("AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Capabilities" in content or "Capabilities" in content
        assert "Project Structure" in content or "Structure" in content
