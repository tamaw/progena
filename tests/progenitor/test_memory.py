"""
Tests for Progenitor runtime memory wiring (Postgres-backed content documents).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class FakeContentStateManager:
    """Minimal async state_manager stub for get/save/list content documents."""

    def __init__(self, *, prompt: str = "GENESIS") -> None:
        self._docs: dict[str, dict[str, str]] = {
            "prompt": {"progenitor/genesis.md": prompt},
            "skill": {},
            "agent": {},
            "memory": {},
        }

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())

    async def list_routine_graphs(self) -> list[dict]:
        return []


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
    """Test that Progenitor wires runtime memory from Postgres content documents."""

    async def test_factory_passes_explicit_memory_paths(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        state_manager = FakeContentStateManager()

        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import create_progenitor_agent

            await create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                state_manager=state_manager,
                memory_paths=["/memories/custom.md", "/memories/notes.md"],
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/memories/custom.md", "/memories/notes.md"]
        # Explicit paths bypass discovery entirely -- nothing written.
        assert state_manager._docs["memory"] == {}

    async def test_factory_defaults_to_postgres_runtime_memory(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        state_manager = FakeContentStateManager()

        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import (
                DEFAULT_RUNTIME_MEMORY_CONTENT,
                create_progenitor_agent,
            )

            await create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                state_manager=state_manager,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == ["/memories/AGENTS.md"]
        assert state_manager._docs["memory"]["AGENTS.md"] == DEFAULT_RUNTIME_MEMORY_CONTENT

    async def test_factory_discovers_postgres_runtime_memories(self, tmp_path: Path):
        config = _fake_config(tmp_path)
        state_manager = FakeContentStateManager()
        state_manager._docs["memory"]["preferences.md"] = "Prefer concise summaries.\n"

        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_model,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create.return_value = MagicMock()
            mock_model.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import create_progenitor_agent

            await create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                state_manager=state_manager,
                project_root=tmp_path,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["memory"] == [
            "/memories/AGENTS.md",
            "/memories/preferences.md",
        ]

    async def test_factory_requires_state_manager(self, tmp_path: Path):
        config = _fake_config(tmp_path)

        from app.progenitor.agent.agent import create_progenitor_agent

        try:
            await create_progenitor_agent(config=config, checkpointer=MagicMock())
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError when state_manager is None")
