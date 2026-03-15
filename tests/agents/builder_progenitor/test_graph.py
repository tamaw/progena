"""Tests for the Builder graph factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_graph_factory_creates_agent_without_middleware():
    with (
        patch("app.agents.builder_progenitor.agent.create_deep_agent") as mock_create,
        patch("app.agents.builder_progenitor.agent.ChatOpenAI") as mock_model,
        patch("app.agents.builder_progenitor.agent.create_builder_tools") as mock_tools,
    ):
        mock_create.return_value = MagicMock()
        mock_model.return_value = MagicMock()
        mock_tools.return_value = []

        from app.agents.builder_progenitor.agent import _create_graph
        from app.agents.builder_progenitor.config import BuilderConfig
        from app.agents.builder_progenitor.prompts import load_prompt

        config = BuilderConfig()
        _create_graph(config, Path.cwd(), Path.cwd() / "logs")

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["name"] == "builder"
        assert call_kwargs["middleware"] == []
        assert call_kwargs["system_prompt"] == load_prompt("system.md").strip()
