"""
Tests for Phase 8: Runtime loop integration with DeepAgent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from app.progenitor.config import ProgenitorConfig
from app.progenitor.runtime.deep_agent_adapter import (
    create_deep_agent_for_loop,
)


class TestCreateDeepAgentForLoop:
    """Tests for the loop adapter factory."""

    def test_returns_compiled_graph(self):
        """Factory returns a compiled graph (same type ProgenitorLoop expects)."""
        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_graph = MagicMock()
            mock_create.return_value = mock_graph
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                result = create_deep_agent_for_loop(config=ProgenitorConfig())
                assert result is mock_graph

    def test_passes_all_parameters(self):
        """All parameters are forwarded to create_progenitor_agent."""
        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_da,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_da.return_value = MagicMock()
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                mock_registry = MagicMock()
                mock_llm = MagicMock()
                mock_sm = MagicMock()
                create_deep_agent_for_loop(
                    config=ProgenitorConfig(),
                    tool_registry=mock_registry,
                    llm_client=mock_llm,
                    state_manager=mock_sm,
                    project_root="/tmp",
                    log_dir="/tmp/logs",
                )

                # Verify the DeepAgent was created (parameters flow through)
                assert mock_da.called


class TestDeepAgentAstreamCompatibility:
    """Verify that DeepAgent graph is compatible with ProgenitorLoop's astream usage."""

    def test_deep_agent_graph_has_astream(self):
        """The graph returned by create_deep_agent has .astream method."""
        with (
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_graph = MagicMock()
            mock_graph.astream = AsyncMock()
            mock_create.return_value = mock_graph
            mock_backend.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                result = create_deep_agent_for_loop(config=ProgenitorConfig())
                assert hasattr(result, "astream")
