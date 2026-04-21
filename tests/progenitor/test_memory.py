"""
Tests for Progenitor runtime memory wiring.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.progenitor.agent.backends import runtime_memory_namespace


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


def _store_item_text(item: object) -> str:
    value = getattr(item, "value", None)
    assert isinstance(value, dict)
    content = value.get("content")
    if isinstance(content, list):
        return "\n".join(content)
    assert isinstance(content, str)
    return content


class TestRuntimeMemoryWiring:
    """Test that Progenitor wires runtime memory through /memories."""

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

    def test_factory_defaults_to_store_backed_runtime_memory(self, tmp_path: Path):
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
                project_root=tmp_path,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/memories/AGENTS.md"]

        store = call_kwargs["store"]
        item = store.get(runtime_memory_namespace(None), "/AGENTS.md")
        assert item is not None
        assert "Progenitor Runtime Memory" in _store_item_text(item)

    def test_factory_migrates_existing_disk_memories_into_store(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        legacy_memory_path = config.storage.data_dir / "memories" / "preferences.md"
        legacy_memory_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_memory_path.write_text("Prefer concise summaries.\n", encoding="utf-8")

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
        assert call_kwargs["memory"] == ["/memories/AGENTS.md", "/memories/preferences.md"]

        store = call_kwargs["store"]
        item = store.get(runtime_memory_namespace(None), "/preferences.md")
        assert item is not None
        assert _store_item_text(item) == "Prefer concise summaries.\n"

    def test_factory_discovers_store_backed_runtime_memories(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        store = MagicMock()
        namespace = runtime_memory_namespace(None)
        store.search.return_value = [
            SimpleNamespace(key="/AGENTS.md"),
            SimpleNamespace(key="/preferences.md"),
        ]
        store.get.return_value = None

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
                store=store,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/memories/AGENTS.md", "/memories/preferences.md"]
        store.search.assert_called_with(namespace, limit=100, offset=0)
