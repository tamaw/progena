"""
Tests for Phase 1: DeepAgent foundation — LangChain adapter and agent factory.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.agent.langchain_adapter import (
    create_azure_structured_model,
    create_langchain_model,
)
from app.progenitor.config import LLMConfig


class TestLangChainAdapter:
    """Tests for the LangChain model adapter."""

    def test_create_model_with_defaults(self):
        """Model created with LiteLLM proxy defaults."""
        config = LLMConfig(
            base_url="http://localhost:4000",
            api_key="test-key",
            model_default="gpt-5.2-chat",
        )
        model = create_langchain_model(config)

        assert model.model_name == "gpt-5.2-chat"
        assert "localhost:4000" in str(model.openai_api_base)
        assert model.temperature == 0
        assert model.streaming is True

    def test_create_model_with_role(self):
        """Role-specific model selection works."""
        config = LLMConfig(
            base_url="http://localhost:4000",
            api_key="test-key",
            model_default="gpt-5.2-chat",
            model_planner="gpt-4.1",
        )
        model = create_langchain_model(config, role="planner")
        assert model.model_name == "gpt-4.1"

    def test_create_model_role_fallback(self):
        """Falls back to default model when role model not configured."""
        config = LLMConfig(
            base_url="http://localhost:4000",
            api_key="test-key",
            model_default="gpt-5.2-chat",
        )
        model = create_langchain_model(config, role="builder")
        assert model.model_name == "gpt-5.2-chat"

    def test_create_model_from_env(self):
        """Model created from environment when no config provided."""
        with patch.dict("os.environ", {
            "LITELLM_BASE_URL": "http://litellm:4000",
            "LITELLM_API_KEY": "env-key",
            "PROGENITOR_MODEL_DEFAULT": "test-model",
        }):
            model = create_langchain_model()
            assert model.model_name == "test-model"

    def test_base_url_trailing_slash_stripped(self):
        """Trailing slash on base_url is stripped."""
        config = LLMConfig(
            base_url="http://localhost:4000/",
            api_key="test-key",
        )
        model = create_langchain_model(config)
        assert not str(model.openai_api_base).endswith("//")

    def test_azure_structured_model_not_configured(self):
        """Returns None when Azure env vars not set."""
        with patch.dict("os.environ", {}, clear=True):
            model = create_azure_structured_model()
            assert model is None

    def test_azure_structured_model_configured(self):
        """Returns model when all Azure env vars set."""
        with patch.dict("os.environ", {
            "PROGENITOR_AZURE_STRUCTURED_RESPONSES_URL": "https://azure.example.com",
            "PROGENITOR_AZURE_STRUCTURED_API_KEY": "azure-key",
            "PROGENITOR_AZURE_STRUCTURED_MODEL": "gpt-4o",
        }):
            model = create_azure_structured_model()
            assert model is not None
            assert model.model_name == "gpt-4o"


class TestDeepAgentFactory:
    """Tests for the DeepAgent factory."""

    def test_create_agent_returns_compiled_graph(self):
        """Factory returns a compiled LangGraph state graph."""
        with patch(
            "app.progenitor.agent.deep_agent.create_langchain_model"
        ) as mock_create_model, patch(
            "app.progenitor.agent.deep_agent.create_deep_agent"
        ) as mock_create_deep_agent:
            mock_model = MagicMock()
            mock_create_model.return_value = mock_model
            mock_graph = MagicMock()
            mock_create_deep_agent.return_value = mock_graph

            from app.progenitor.agent.deep_agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            config = ProgenitorConfig()
            result = create_progenitor_agent(config=config)

            assert result is mock_graph
            mock_create_model.assert_called_once_with(config.llm)
            mock_create_deep_agent.assert_called_once()

            # Verify key arguments passed to create_deep_agent
            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["name"] == "progenitor"
            assert call_kwargs.kwargs["model"] is mock_model
            assert "system_prompt" in call_kwargs.kwargs

    def test_create_agent_uses_custom_checkpointer(self):
        """Custom checkpointer is passed through."""
        with patch(
            "app.progenitor.agent.deep_agent.create_langchain_model"
        ) as mock_create_model, patch(
            "app.progenitor.agent.deep_agent.create_deep_agent"
        ) as mock_create_deep_agent:
            mock_create_model.return_value = MagicMock()
            mock_create_deep_agent.return_value = MagicMock()

            from app.progenitor.agent.deep_agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            custom_checkpointer = MagicMock()
            create_progenitor_agent(
                config=ProgenitorConfig(),
                checkpointer=custom_checkpointer,
            )

            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["checkpointer"] is custom_checkpointer
