"""Tests for the Builder Copilot DeepAgent factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_factory_passes_relative_skills_root():
    with patch("app.agents.builder_copilot.deep_agent.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        with patch("app.agents.builder_copilot.deep_agent.create_langchain_model") as mock_model:
            mock_model.return_value = MagicMock()
            with patch("app.agents.builder_copilot.deep_agent.create_builder_copilot_tools") as mock_tools:
                mock_tools.return_value = []

                from app.agents.builder_copilot.deep_agent import create_builder_copilot_agent

                create_builder_copilot_agent(project_root=Path.cwd())

                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs["name"] == "builder-copilot"
                assert call_kwargs["skills"] == ["app/agents/builder_copilot/skills"]
