"""Tests for Builder Agent graph guards."""

import pytest


def test_create_inner_loop_graph_requires_copilot_acp():
    from app.agents.builder_agent.graph import create_inner_loop_graph

    with pytest.raises(RuntimeError, match="copilot_acp_manager is required"):
        create_inner_loop_graph(
            state_manager=None,
            llm_client=None,
            config=None,
            worktrees_dir=None,
            copilot_acp_manager=None,
            checkpoint_db_path=False,
        )
