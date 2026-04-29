"""
Tests for Phase 1: DeepAgent foundation — LangChain adapter and agent factory.
"""

from unittest.mock import MagicMock, patch

from app.progenitor.agent.langchain_adapter import create_langchain_model
from app.progenitor.config import LLMConfig


class TestLangChainAdapter:
    """Tests for the LangChain model adapter."""

    def test_create_model_with_defaults(self):
        """Model created with configured Azure endpoint."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.2-chat",
        )
        model = create_langchain_model(config)

        assert model.model_name == "gpt-5.2-chat"
        assert "azure.com" in str(model.openai_api_base)
        assert model.streaming is True
        assert model.use_responses_api is True
        assert model.output_version == "responses/v1"

    def test_create_model_from_env(self):
        """Model created from environment when no config provided."""
        with patch.dict(
            "os.environ",
            {
                "AZURE_OPENAI_BASE_URL": "https://env.openai.azure.com/openai/v1",
                "AZURE_OPENAI_API_KEY": "env-key",
                "PROGENITOR_MODEL_DEFAULT": "test-model",
            },
        ):
            model = create_langchain_model()
            assert model.model_name == "test-model"

    def test_base_url_trailing_slash_stripped(self):
        """Trailing slash on base_url is stripped."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1/",
            api_key="test-key",
        )
        model = create_langchain_model(config)
        assert not str(model.openai_api_base).endswith("//")

    def test_base_url_with_responses_query_normalized(self):
        """Full responses endpoint URL is normalized for LangChain base_url."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1/responses?api-version=preview",
            api_key="test-key",
        )
        model = create_langchain_model(config)
        assert str(model.openai_api_base) == "https://example.openai.azure.com/openai/v1"

    def test_reasoning_uses_effort_with_summary_extra_body(self):
        """Reasoning config requests summary text via extra_body with the resolved effort."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.2-chat",
            reasoning_enabled=True,
            reasoning_effort="medium",
            reasoning_summary="detailed",
        )
        with patch("app.progenitor.agent.langchain_adapter.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "medium"
        assert kwargs["extra_body"] == {"reasoning": {"summary": "detailed"}}

    def test_reasoning_defaults_to_low_effort(self):
        """Reasoning defaults to low effort for standard models."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.2-chat",
            reasoning_enabled=True,
        )
        with patch("app.progenitor.agent.langchain_adapter.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["extra_body"] == {"reasoning": {"summary": "auto"}}

    def test_reasoning_effort_respects_config_for_gpt_5_3_chat(self):
        """gpt-5.3-chat uses the configured reasoning effort."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.3-chat-2026-03-03",
            reasoning_enabled=True,
            reasoning_effort="medium",
        )
        with patch("app.progenitor.agent.langchain_adapter.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "medium"
        assert kwargs["extra_body"] == {"reasoning": {"summary": "auto"}}

    def test_reasoning_summary_defaults_to_auto(self):
        """Reasoning summary defaults to auto when not overridden."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.2-chat",
            reasoning_enabled=True,
        )
        with patch("app.progenitor.agent.langchain_adapter.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["extra_body"] == {"reasoning": {"summary": "auto"}}


class TestDeepAgentFactory:
    """Tests for the DeepAgent factory."""

    def test_create_agent_returns_compiled_graph(self):
        """Factory returns a compiled LangGraph state graph."""
        with (
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_model = MagicMock()
            mock_create_model.return_value = mock_model
            mock_graph = MagicMock()
            mock_create_deep_agent.return_value = mock_graph
            mock_backend.return_value = MagicMock()

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
            assert "backend" in call_kwargs.kwargs
            assert "store" in call_kwargs.kwargs

    def test_create_agent_uses_custom_checkpointer(self):
        """Custom checkpointer is passed through."""
        with (
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create_model.return_value = MagicMock()
            mock_create_deep_agent.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.deep_agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            custom_checkpointer = MagicMock()
            create_progenitor_agent(
                config=ProgenitorConfig(),
                checkpointer=custom_checkpointer,
            )

            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["checkpointer"] is custom_checkpointer

    def test_create_agent_uses_genesis_prompt(self):
        """DeepAgent factory uses the canonical genesis prompt."""
        with (
            patch("app.progenitor.agent.deep_agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.deep_agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.deep_agent.PromptTemplates") as mock_prompt_templates,
            patch("app.progenitor.agent.deep_agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create_model.return_value = MagicMock()
            mock_create_deep_agent.return_value = MagicMock()
            mock_prompt_templates.return_value.get_genesis_prompt.return_value = "GENESIS"
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.deep_agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            config = ProgenitorConfig()
            create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
            )

            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["system_prompt"] == "GENESIS"
            mock_prompt_templates.assert_called_once_with(prompts_dir=config.storage.prompts_dir)
            mock_prompt_templates.return_value.get_genesis_prompt.assert_called_once()
