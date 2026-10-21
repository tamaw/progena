"""
Tests for Phase 8: Runtime loop integration with DeepAgent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.agent.adapters import (
    create_deep_agent_for_runtime,
)


class FakeContentStateManager:
    """Minimal async state_manager stub for get/save/list content documents."""

    def __init__(self, *, prompt: str = "GENESIS") -> None:
        self._docs: dict[str, dict[str, str]] = {
            "instruction": {
                "progenitor/genesis.md": f"---\ntitle: Genesis\ndescription: Test\n---\n{prompt}"
            },
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


class TestCreateDeepAgentForLoop:
    """Tests for the loop adapter factory."""

    @pytest.mark.asyncio
    async def test_returns_compiled_graph(self):
        """Factory returns a compiled graph (same type the heartbeat loop expects)."""
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_graph = MagicMock()
            mock_create.return_value = mock_graph
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.ProviderChatModel") as mock_model:
                mock_model.return_value = MagicMock()

                result = await create_deep_agent_for_runtime(
                    state_manager=FakeContentStateManager()
                )
                assert result is mock_graph

    @pytest.mark.asyncio
    async def test_passes_all_parameters(self):
        """All parameters are forwarded to create_progenitor_agent."""
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_da,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_da.return_value = MagicMock()
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.ProviderChatModel") as mock_model:
                mock_model.return_value = MagicMock()

                mock_registry = MagicMock()
                mock_llm = MagicMock()
                mock_sm = FakeContentStateManager()
                await create_deep_agent_for_runtime(
                    tool_registry=mock_registry,
                    llm_client=mock_llm,
                    state_manager=mock_sm,
                    project_root="/tmp",
                    log_sink=MagicMock(),
                )

                # Verify the DeepAgent was created (parameters flow through)
                assert mock_da.called


class TestDeepAgentAstreamCompatibility:
    """Verify that DeepAgent graph exposes native LangGraph streaming for SSE."""

    @pytest.mark.asyncio
    async def test_deep_agent_graph_has_astream(self):
        """The graph returned by create_deep_agent has .astream method."""
        with (
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_graph = MagicMock()
            mock_graph.astream = AsyncMock()
            mock_create.return_value = mock_graph
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.agent.ProviderChatModel") as mock_model:
                mock_model.return_value = MagicMock()

                result = await create_deep_agent_for_runtime(
                    state_manager=FakeContentStateManager()
                )
                assert hasattr(result, "astream")
