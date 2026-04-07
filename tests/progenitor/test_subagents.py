"""
Tests for Phase 7: Subagent architecture.
"""

from unittest.mock import MagicMock, patch

from app.progenitor.agent.subagents import (
    create_build_subagent,
    create_code_review_subagent,
    create_progenitor_subagents,
    create_research_subagent,
)


class TestBuildSubagent:
    """Tests for the build loop subagent."""

    def test_returns_none_without_graph(self):
        result = create_build_subagent(inner_loop_graph=None)
        assert result is None

    def test_returns_compiled_subagent_with_graph(self):
        mock_graph = MagicMock()
        result = create_build_subagent(inner_loop_graph=mock_graph)
        assert isinstance(result, dict)  # TypedDict is a dict
        assert result["name"] == "build-agent"
        assert "description" in result
        assert result["runnable"] is mock_graph

    def test_description_mentions_build_loop(self):
        mock_graph = MagicMock()
        result = create_build_subagent(inner_loop_graph=mock_graph)
        desc = result["description"]
        assert "OBSERVE" in desc
        assert "VERIFY" in desc
        assert "LEARN" in desc


class TestCodeReviewSubagent:
    """Tests for the code review subagent."""

    def test_returns_subagent(self):
        result = create_code_review_subagent()
        assert isinstance(result, dict)
        assert result["name"] == "code-reviewer"

    def test_has_system_prompt(self):
        result = create_code_review_subagent()
        assert "system_prompt" in result
        assert len(result["system_prompt"]) > 20

    def test_no_tools(self):
        """Review is read-only — no tools."""
        result = create_code_review_subagent()
        assert result.get("tools", []) == []


class TestResearchSubagent:
    """Tests for the research subagent."""

    def test_returns_subagent(self):
        result = create_research_subagent()
        assert isinstance(result, dict)
        assert result["name"] == "researcher"

    def test_has_description(self):
        result = create_research_subagent()
        assert "research" in result["description"].lower()

    def test_inherits_parent_tools(self):
        result = create_research_subagent()
        assert "tools" not in result


class TestCreateProgenitorSubagents:
    """Tests for the combined subagent factory."""

    def test_without_build_loop(self):
        result = create_progenitor_subagents(inner_loop_graph=None)
        names = [s["name"] for s in result]
        assert "code-reviewer" in names
        assert "researcher" in names
        assert "build-agent" not in names

    def test_with_build_loop(self):
        mock_graph = MagicMock()
        result = create_progenitor_subagents(inner_loop_graph=mock_graph)
        names = [s["name"] for s in result]
        assert "build-agent" in names
        assert "code-reviewer" in names
        assert "researcher" in names
        assert len(result) == 3

    def test_subagent_names_unique(self):
        mock_graph = MagicMock()
        result = create_progenitor_subagents(inner_loop_graph=mock_graph)
        names = [s["name"] for s in result]
        assert len(names) == len(set(names))


class TestSubagentWiring:
    """Test subagents are wired into the factory."""

    def test_factory_passes_subagents(self):
        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_progenitor_subagents") as mock_subagents,
        ):
            mock_create.return_value = MagicMock()
            mock_subagents.return_value = [{"name": "researcher"}]
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.deep_agent import create_progenitor_agent

                create_progenitor_agent()

                call_kwargs = mock_create.call_args.kwargs
                assert "subagents" in call_kwargs
                assert call_kwargs["subagents"] == [{"name": "researcher"}]
                mock_subagents.assert_called_once_with(inner_loop_graph=None)

    def test_factory_with_inner_loop(self):
        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_progenitor_subagents") as mock_subagents,
        ):
            mock_create.return_value = MagicMock()
            mock_subagents.return_value = [{"name": "build-agent"}]
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.deep_agent import create_progenitor_agent

                mock_graph = MagicMock()
                create_progenitor_agent(inner_loop_graph=mock_graph)

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["subagents"] == [{"name": "build-agent"}]
                mock_subagents.assert_called_once_with(inner_loop_graph=mock_graph)
