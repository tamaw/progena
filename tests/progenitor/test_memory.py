"""
Tests for Progenitor runtime memory wiring.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _fake_config(base_dir: Path) -> SimpleNamespace:
    data_dir = base_dir / "data"
    prompts_dir = data_dir / "prompts"
    state_dir = data_dir / "state"
    log_dir = base_dir / "logs"
    for path in (data_dir, prompts_dir, state_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(
        llm=SimpleNamespace(model_default="test-model"),
        storage=SimpleNamespace(
            data_dir=data_dir,
            state_dir=state_dir,
            log_dir=log_dir,
            prompts_dir=prompts_dir,
        ),
    )


class TestRuntimeMemoryWiring:
    """Test that Progenitor wires runtime memory from the filesystem."""

    def test_factory_passes_explicit_memory_paths(self, tmp_path: Path):
        config = _fake_config(tmp_path)

        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.deep_agent.PromptTemplates") as mock_prompts,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_prompts.return_value.get_genesis_prompt.return_value = "GENESIS"

            from app.progenitor.agent.deep_agent import create_progenitor_agent

            create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                memory_paths=["/memories/custom.md", "/memories/notes.md"],
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/memories/custom.md", "/memories/notes.md"]

    def test_factory_defaults_to_filesystem_runtime_memory(self, tmp_path: Path):
        config = _fake_config(tmp_path)

        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.deep_agent.PromptTemplates") as mock_prompts,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_prompts.return_value.get_genesis_prompt.return_value = "GENESIS"

            from app.progenitor.agent.deep_agent import (
                DEFAULT_RUNTIME_MEMORY_CONTENT,
                create_progenitor_agent,
            )

            create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                project_root=tmp_path,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/data/memories/AGENTS.md"]
        memory_file = config.storage.data_dir / "memories" / "AGENTS.md"
        assert memory_file.read_text(encoding="utf-8") == DEFAULT_RUNTIME_MEMORY_CONTENT

    def test_factory_discovers_filesystem_runtime_memories(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        memory_file = config.storage.data_dir / "memories" / "preferences.md"
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        memory_file.write_text("Prefer concise summaries.\n", encoding="utf-8")

        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.deep_agent.PromptTemplates") as mock_prompts,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_prompts.return_value.get_genesis_prompt.return_value = "GENESIS"

            from app.progenitor.agent.deep_agent import create_progenitor_agent

            create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                project_root=tmp_path,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == [
            "/data/memories/AGENTS.md",
            "/data/memories/preferences.md",
        ]
