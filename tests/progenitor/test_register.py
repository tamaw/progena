"""Tests for register node and LiteLLM registration."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.state.models import ProgenitorState


class TestRegisterWithLiteLLM:
    """Tests for the _register_with_litellm helper."""

    @pytest.mark.asyncio
    async def test_successful_registration(self):
        """Should return True when LiteLLM responds with success."""
        from app.progenitor.nodes.register import _register_with_litellm

        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.progenitor.nodes.register.httpx.AsyncClient", return_value=mock_client):
            result = await _register_with_litellm(
                model_name="agent/weather",
                api_base="http://localhost:8100/v1",
                description="Weather agent",
            )

        assert result is True
        mock_client.post.assert_called_once()

        # Verify the payload structure
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["model_name"] == "agent/weather"
        assert payload["litellm_params"]["model"] == "openai/agent/weather"
        assert payload["litellm_params"]["api_base"] == "http://localhost:8100/v1"

    @pytest.mark.asyncio
    async def test_failed_registration(self):
        """Should return False when LiteLLM responds with error."""
        from app.progenitor.nodes.register import _register_with_litellm

        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 400
        mock_response.text = "Bad request"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.progenitor.nodes.register.httpx.AsyncClient", return_value=mock_client):
            result = await _register_with_litellm(
                model_name="agent/broken",
                api_base="http://localhost:8100/v1",
                description="Broken agent",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_handles_connection_error(self):
        """Should return False and not raise when LiteLLM is unreachable."""
        from app.progenitor.nodes.register import _register_with_litellm

        mock_client = AsyncMock()
        mock_client.post.side_effect = ConnectionError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.progenitor.nodes.register.httpx.AsyncClient", return_value=mock_client):
            result = await _register_with_litellm(
                model_name="agent/test",
                api_base="http://localhost:8100/v1",
                description="test",
            )

        assert result is False


class TestRegisterNode:
    """Tests for the register_node function."""

    def _make_state(self, **overrides) -> ProgenitorState:
        state: ProgenitorState = {
            "current_work_item": None,
            "validation_result_v2": None,
            "messages": [],
        }
        state.update(overrides)
        return state

    def _make_deps(self):
        """Create mock dependencies for register node."""
        tool_registry = MagicMock()
        tool_registry.allocate_port.return_value = 8100
        tool_registry.get_agents.return_value = []
        tool_registry.register_agent = AsyncMock()
        tool_registry.register_tool = AsyncMock()
        tool_registry.find_by_name.return_value = None

        state_manager = AsyncMock()

        config = MagicMock()
        config.storage.worktree_dir = Path("/tmp/worktrees")

        return tool_registry, state_manager, config

    @pytest.mark.asyncio
    async def test_skips_when_no_work_item(self):
        """Should return empty dict when no current_work_item."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        node = register.create_node(registry, sm, config)
        result = await node(self._make_state())
        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_when_validation_failed(self):
        """Should skip when validation has blocking issues."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        node = register.create_node(registry, sm, config)
        state = self._make_state(
            current_work_item={
                "id": "item-1",
                "item_type": "build_agent",
                "description": "test",
                "result": {"spec": {"name": "test"}},
            },
            validation_result_v2={"has_blocking_issues": True},
        )
        result = await node(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_registers_agent_and_calls_litellm(self):
        """Should register agent in tool registry AND with LiteLLM."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()

        with patch.object(register, "AgentRunner") as MockRunner:
            mock_runner = AsyncMock()
            test_result = MagicMock()
            test_result.success = True
            test_result.response = "OK"
            test_result.error = None
            test_result.duration_ms = 100
            mock_runner.test_agent.return_value = test_result
            MockRunner.return_value = mock_runner

            with patch.object(
                register, "_register_with_litellm", new_callable=AsyncMock
            ) as mock_litellm:
                mock_litellm.return_value = True

                node = register.create_node(registry, sm, config)
                state = self._make_state(
                    current_work_item={
                        "id": "item-1",
                        "item_type": "build_agent",
                        "description": "test",
                        "result": {
                            "spec": {
                                "name": "weather_agent",
                                "description": "Weather agent",
                                "capabilities": ["forecast"],
                            }
                        },
                    },
                    validation_result_v2={"has_blocking_issues": False},
                )

                await node(state)

        # Should have registered with tool registry
        registry.register_agent.assert_called_once()

        # Should have called LiteLLM registration
        mock_litellm.assert_called_once_with(
            model_name="agent/weather_agent",
            api_base="http://localhost:8100/v1",
            description="Weather agent",
        )

        # Should have tested the agent
        mock_runner.test_agent.assert_called_once()

        # Should have logged decisions
        assert sm.log_decision.call_count >= 2  # tested + registered

    @pytest.mark.asyncio
    async def test_registers_mcp_tool(self):
        """Should register MCP tool and call LiteLLM."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()

        with patch.object(register, "AgentRunner") as MockRunner:
            MockRunner.return_value = AsyncMock()

            with patch.object(
                register, "_register_with_litellm", new_callable=AsyncMock
            ) as mock_litellm:
                mock_litellm.return_value = True

                node = register.create_node(registry, sm, config)
                state = self._make_state(
                    current_work_item={
                        "id": "item-2",
                        "item_type": "build_mcp",
                        "description": "test",
                        "result": {
                            "spec": {
                                "name": "search_tool",
                                "description": "Web search",
                                "capabilities": ["search"],
                            }
                        },
                    },
                    validation_result_v2={"has_blocking_issues": False},
                )

                await node(state)

        # Should have registered tool
        registry.register_tool.assert_called_once()

        # Should have called LiteLLM
        mock_litellm.assert_called_once()
        assert mock_litellm.call_args.kwargs["model_name"] == "mcp/search_tool"

    @pytest.mark.asyncio
    async def test_handles_registration_exception(self):
        """Should catch and log exceptions, returning empty dict."""
        from app.progenitor.nodes import register

        registry, sm, config = self._make_deps()
        registry.allocate_port.side_effect = RuntimeError("port allocation failed")

        with patch.object(register, "AgentRunner") as MockRunner:
            MockRunner.return_value = AsyncMock()

            node = register.create_node(registry, sm, config)
            state = self._make_state(
                current_work_item={
                    "id": "item-1",
                    "item_type": "build_agent",
                    "description": "test",
                    "result": {"spec": {"name": "test"}},
                },
                validation_result_v2={"has_blocking_issues": False},
            )

            result = await node(state)

        # Should return empty (error caught)
        assert result == {}
