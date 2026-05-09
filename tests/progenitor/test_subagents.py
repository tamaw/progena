"""Tests for active DeepAgent subagent definitions."""

from unittest.mock import MagicMock, patch

from app.progenitor.agent.subagents import (
    create_code_review_subagent,
    create_progenitor_subagents,
    create_research_subagent,
)


class TestCodeReviewSubagent:
    def test_returns_subagent(self):
        result = create_code_review_subagent()
        assert isinstance(result, dict)
        assert result["name"] == "code-reviewer"

    def test_has_system_prompt(self):
        result = create_code_review_subagent()
        assert "system_prompt" in result
        assert len(result["system_prompt"]) > 20

    def test_no_tools(self):
        result = create_code_review_subagent()
        assert result.get("tools", []) == []


class TestResearchSubagent:
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
    def test_creates_active_subagents(self):
        result = create_progenitor_subagents()
        names = [s["name"] for s in result]
        assert names == ["code-reviewer", "researcher"]

    def test_subagent_names_unique(self):
        result = create_progenitor_subagents()
        names = [s["name"] for s in result]
        assert len(names) == len(set(names))


class TestSubagentWiring:
    def test_factory_passes_subagents(self):
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_subagents") as mock_subagents,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_subagents.return_value = [{"name": "researcher"}]
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()
                from app.progenitor.agent.agent import create_progenitor_agent

                create_progenitor_agent()

                call_kwargs = mock_create.call_args.kwargs
                assert "subagents" in call_kwargs
                assert call_kwargs["subagents"] == [{"name": "researcher"}]
                mock_subagents.assert_called_once_with()
