"""
Tests for app/progenitor/dependencies.py

Verifies NodeDependencies dataclass for dependency injection.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from app.progenitor.dependencies import NodeDependencies


class TestNodeDependenciesCreation:
    """Tests for NodeDependencies creation."""

    def test_minimal_creation(self):
        """Should create with just llm_client and state_manager."""
        llm_client = MagicMock()
        state_manager = MagicMock()

        deps = NodeDependencies(
            llm_client=llm_client,
            state_manager=state_manager,
        )

        assert deps.llm_client is llm_client
        assert deps.state_manager is state_manager
        assert deps.tool_registry is None
        assert deps.config is None
        assert deps.worktrees_dir is None
        assert deps.event_pusher is None

    def test_full_creation(self):
        """Should create with all fields."""
        llm_client = MagicMock()
        state_manager = MagicMock()
        tool_registry = MagicMock()
        config = MagicMock()
        event_pusher = AsyncMock()

        deps = NodeDependencies(
            llm_client=llm_client,
            state_manager=state_manager,
            tool_registry=tool_registry,
            config=config,
            worktrees_dir=Path("/path/to/worktrees"),
            event_pusher=event_pusher,
        )

        assert deps.llm_client is llm_client
        assert deps.state_manager is state_manager
        assert deps.tool_registry is tool_registry
        assert deps.config is config
        assert deps.worktrees_dir == Path("/path/to/worktrees")
        assert deps.event_pusher is event_pusher


class TestNodeDependenciesDefaults:
    """Tests for NodeDependencies default values."""

    def test_optional_fields_default_to_none(self):
        """Optional fields should default to None."""
        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
        )

        assert deps.tool_registry is None
        assert deps.config is None
        assert deps.worktrees_dir is None
        assert deps.event_pusher is None


class TestNodeDependenciesImmutability:
    """Tests for NodeDependencies field access."""

    def test_fields_are_accessible(self):
        """All fields should be accessible after creation."""
        llm_client = MagicMock()
        state_manager = MagicMock()

        deps = NodeDependencies(
            llm_client=llm_client,
            state_manager=state_manager,
        )

        # Fields should be accessible
        _ = deps.llm_client
        _ = deps.state_manager
        _ = deps.tool_registry
        _ = deps.config
        _ = deps.worktrees_dir
        _ = deps.event_pusher


class TestNodeDependenciesWithRealTypes:
    """Tests with realistic dependency types."""

    def test_with_async_event_pusher(self):
        """Should handle async event pusher correctly."""
        from typing import Any, Optional

        async def push_event(event_type: str, event_name: str, data: Optional[dict[str, Any]] = None) -> None:
            pass

        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
            event_pusher=push_event,
        )

        assert deps.event_pusher is push_event
        assert callable(deps.event_pusher)

    def test_with_path_worktrees_dir(self):
        """Should handle Path objects for worktrees_dir."""
        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
            worktrees_dir=Path("d:/tmp/agent/worktrees"),
        )

        assert deps.worktrees_dir == Path("d:/tmp/agent/worktrees")


class TestNodeDependenciesUsagePatterns:
    """Tests for common usage patterns."""

    def test_can_use_for_node_factory(self):
        """Should work as argument to node factory functions."""
        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
        )

        # Simulate node factory usage
        def create_node(deps: NodeDependencies):
            async def node(state):
                # Use deps
                _ = deps.llm_client
                _ = deps.state_manager
                return {}
            return node

        node_fn = create_node(deps)
        assert callable(node_fn)

    def test_can_be_shared_across_nodes(self):
        """Same deps instance should be usable by multiple nodes."""
        deps = NodeDependencies(
            llm_client=MagicMock(),
            state_manager=MagicMock(),
        )

        # Simulate multiple node factories
        def create_node_a(deps):
            return lambda state: {"from": "a", "llm": deps.llm_client}

        def create_node_b(deps):
            return lambda state: {"from": "b", "llm": deps.llm_client}

        node_a = create_node_a(deps)
        node_b = create_node_b(deps)

        # Both should use the same llm_client
        result_a = node_a({})
        result_b = node_b({})

        assert result_a["llm"] is result_b["llm"]
