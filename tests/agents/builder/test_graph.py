"""Tests for the Builder graph factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch


def test_graph_factory_creates_agent_without_middleware():
    project_root = Path(__file__).resolve().parents[3]
    with (
        patch("app.agents.builder.agent.create_deep_agent") as mock_create,
        patch("app.agents.builder.agent.ChatOpenAI") as mock_model,
        patch("app.agents.builder.agent.create_builder_tools") as mock_tools,
    ):
        mock_create.return_value = MagicMock()
        mock_model.return_value = MagicMock()
        mock_tools.return_value = []

        from app.agents.builder.agent import _create_graph
        from app.agents.builder.config import BuilderConfig
        from app.agents.builder.prompts import load_prompt

        config = BuilderConfig()
        _create_graph(
            config,
            project_root,
            project_root / "logs",
            name="builder",
            system_prompt=load_prompt("system.md").strip(),
        )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["name"] == "builder"
        assert call_kwargs["middleware"] == []
        assert call_kwargs["system_prompt"] == load_prompt("system.md").strip()
        assert str(project_root / "AGENTS.md") in call_kwargs["memory"]
        assert any(path.endswith("app/agents/builder/skills") for path in call_kwargs["skills"])
        assert any(path.endswith("data/skills/builder") for path in call_kwargs["skills"])
        assert not any(path.endswith("data/skills/progenitor") for path in call_kwargs["skills"])
