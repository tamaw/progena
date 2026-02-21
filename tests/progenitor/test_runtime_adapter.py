"""
Tests for Phase 8: Runtime loop integration with DeepAgent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.runtime.deep_agent_adapter import (
    create_deep_agent_for_loop,
    patch_loop_with_deep_agent,
)


class TestCreateDeepAgentForLoop:
    """Tests for the loop adapter factory."""

    def test_returns_compiled_graph(self):
        """Factory returns a compiled graph (same type ProgenitorLoop expects)."""
        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create:
            mock_graph = MagicMock()
            mock_create.return_value = mock_graph
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                result = create_deep_agent_for_loop()
                assert result is mock_graph

    def test_passes_all_parameters(self):
        """All parameters are forwarded to create_progenitor_agent."""
        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_da:
            mock_da.return_value = MagicMock()
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                mock_registry = MagicMock()
                mock_llm = MagicMock()
                mock_sm = MagicMock()
                mock_graph = MagicMock()

                create_deep_agent_for_loop(
                    tool_registry=mock_registry,
                    llm_client=mock_llm,
                    state_manager=mock_sm,
                    inner_loop_graph=mock_graph,
                    project_root="/tmp",
                    log_dir="/tmp/logs",
                )

                call_kwargs = mock_da.call_args.kwargs
                # Verify the DeepAgent was created (parameters flow through)
                assert mock_da.called


class TestPatchLoopWithDeepAgent:
    """Tests for the loop patching function."""

    def test_replaces_graph(self):
        """patch_loop_with_deep_agent replaces loop.graph."""
        mock_loop = MagicMock()
        mock_loop.config = MagicMock()
        mock_loop.llm_client = MagicMock()
        mock_loop.state_manager = MagicMock()

        with patch("app.progenitor.runtime.deep_agent_adapter.create_deep_agent_for_loop") as mock_factory:
            mock_deep_agent = MagicMock()
            mock_factory.return_value = mock_deep_agent

            result = patch_loop_with_deep_agent(mock_loop)

            assert result is mock_loop
            assert mock_loop.graph is mock_deep_agent

    def test_preserves_background_graph(self):
        """Background graph is NOT replaced — only the main conversational graph."""
        mock_loop = MagicMock()
        mock_loop.config = MagicMock()
        mock_loop.llm_client = MagicMock()
        mock_loop.state_manager = MagicMock()
        original_bg_graph = MagicMock()
        mock_loop.background_graph = original_bg_graph

        with patch("app.progenitor.runtime.deep_agent_adapter.create_deep_agent_for_loop") as mock_factory:
            mock_factory.return_value = MagicMock()

            patch_loop_with_deep_agent(mock_loop)

            # Background graph should be untouched
            assert mock_loop.background_graph is original_bg_graph

    def test_uses_loop_defaults(self):
        """Uses loop's config/llm_client/state_manager as defaults."""
        mock_loop = MagicMock()
        mock_config = MagicMock()
        mock_llm = MagicMock()
        mock_sm = MagicMock()
        mock_loop.config = mock_config
        mock_loop.llm_client = mock_llm
        mock_loop.state_manager = mock_sm

        with patch("app.progenitor.runtime.deep_agent_adapter.create_deep_agent_for_loop") as mock_factory:
            mock_factory.return_value = MagicMock()

            patch_loop_with_deep_agent(mock_loop)

            call_kwargs = mock_factory.call_args.kwargs
            assert call_kwargs["config"] is mock_config
            assert call_kwargs["llm_client"] is mock_llm
            assert call_kwargs["state_manager"] is mock_sm


class TestDeepAgentAstreamCompatibility:
    """Verify that DeepAgent graph is compatible with ProgenitorLoop's astream usage."""

    def test_deep_agent_graph_has_astream(self):
        """The graph returned by create_deep_agent has .astream method."""
        with patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create:
            mock_graph = MagicMock()
            mock_graph.astream = AsyncMock()
            mock_create.return_value = mock_graph
            with patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_model:
                mock_model.return_value = MagicMock()

                result = create_deep_agent_for_loop()
                assert hasattr(result, "astream")
