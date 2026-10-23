"""
Tests for Phase 1: DeepAgent foundation — LangChain adapter and agent factory.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from src.progena.agent import adapters
from src.progena.agent.adapters import ProviderChatModel, create_langchain_model
from src.progena.config import LLMConfig
from src.progena.llm.provider_context import ProviderNotConfiguredError, ProviderTurn


@pytest.mark.parametrize("asynchronous", [False, True])
def test_generation_uses_completed_responses_with_real_client(monkeypatch, asynchronous):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text='data: {"type":"response.completed","response":{"id":"resp_test","error":null}}\n\n',
            )
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "test-model",
                "error": None,
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "The answer is 42.", "annotations": []}
                        ],
                    }
                ],
            },
        )

    real_client = adapters.CheckpointChatOpenAI
    transport = httpx.MockTransport(respond)

    def client(**kwargs):
        return real_client(
            **kwargs,
            http_client=httpx.Client(transport=transport),
            http_async_client=httpx.AsyncClient(transport=transport),
        )

    async def resolve(*args, **kwargs):
        return LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="test-model",
        )

    monkeypatch.setattr(adapters, "CheckpointChatOpenAI", client)
    turn = ProviderTurn(template=LLMConfig(), session_id="generation-test", _connections={})
    monkeypatch.setattr(adapters, "current_provider_turn", lambda: turn)
    monkeypatch.setattr(adapters, "provider_config_for_call", resolve)
    model = ProviderChatModel().bind_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up a value.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice="auto",
    )
    messages = [[HumanMessage(content="What is the answer?")]]
    result = asyncio.run(model.agenerate(messages)) if asynchronous else model.generate(messages)

    assert result.generations[0][0].message.content[0]["text"] == "The answer is 42."
    assert requests[0]["stream"] is False
    assert requests[0]["tools"][0]["name"] == "lookup"
    assert requests[0]["tool_choice"] == "auto"


class _GenerationBoundaryModel(BaseChatModel):
    """Local chat-model boundary that records private generation hook inputs."""

    calls: list[tuple[str, dict[str, Any]]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "generation_boundary"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def with_structured_output(
        self, schema: dict[str, Any] | type, *, include_raw: bool = False, **kwargs: Any
    ) -> Any:
        bound = self.bind(response_format=schema, **kwargs)
        if include_raw:
            return bound | RunnableLambda(
                lambda message: {
                    "raw": message,
                    "parsed": {"answer": message.content},
                    "parsing_error": None,
                }
            )
        return bound | RunnableLambda(lambda message: {"answer": message.content})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(("generate", {"stop": stop, **kwargs}))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="raw"))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(("agenerate", {"stop": stop, **kwargs}))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="raw"))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        self.calls.append(("stream", {"stop": stop, **kwargs}))
        yield ChatGenerationChunk(message=AIMessageChunk(content="chunk"))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.calls.append(("astream", {"stop": stop, **kwargs}))
        yield ChatGenerationChunk(message=AIMessageChunk(content="chunk"))


def _install_generation_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: _GenerationBoundaryModel
) -> None:
    async def resolve_provider_config(*args: Any, **kwargs: Any) -> LLMConfig:
        return LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="test-model",
        )

    monkeypatch.setattr(adapters, "current_provider_turn", lambda: object())
    monkeypatch.setattr(adapters, "provider_config_for_call", resolve_provider_config)
    monkeypatch.setattr(adapters, "create_langchain_model", lambda config, **kwargs: boundary)


class TestLangChainAdapter:
    """Tests for the LangChain model adapter."""

    def test_create_model_with_defaults(self):
        """Model created with configured Azure endpoint."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-chat-latest",
        )
        model = create_langchain_model(config)

        assert model.model_name == "gpt-chat-latest"
        assert "azure.com" in str(model.openai_api_base)
        assert model.streaming is True
        assert model.use_responses_api is True
        assert model.output_version == "responses/v1"

    def test_chatgpt_model_retains_registration_for_unauthorized_recovery(self):
        config = LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="oauth-token",
            model_default="gpt-test",
            provider_kind="chatgpt",
            provider_id="provider-1",
        )
        with patch("src.progena.agent.adapters.CheckpointChatOpenAI") as mock_chat_openai:
            model = mock_chat_openai.return_value
            create_langchain_model(config)

        model.set_chatgpt_provider.assert_called_once_with("provider-1", "oauth-token")

    def test_create_model_requires_a_resolved_provider_snapshot(self):
        """No process environment fallback may manufacture a model."""
        with pytest.raises(ProviderNotConfiguredError):
            create_langchain_model()

    def test_base_url_trailing_slash_stripped(self):
        """Trailing slash on base_url is stripped."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1/",
            api_key="test-key",
            model_default="gpt-chat-latest",
        )
        model = create_langchain_model(config)
        assert not str(model.openai_api_base).endswith("//")

    def test_base_url_with_responses_query_normalized(self):
        """Full responses endpoint URL is normalized for LangChain base_url."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1/responses?api-version=preview",
            api_key="test-key",
            model_default="gpt-chat-latest",
        )
        model = create_langchain_model(config)
        assert str(model.openai_api_base) == "https://example.openai.azure.com/openai/v1"

    def test_reasoning_uses_azure_compatible_payload(self):
        """Reasoning uses effort plus the Azure-compatible summary body."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-chat-latest",
            reasoning_enabled=True,
            reasoning_effort="medium",
            reasoning_summary="detailed",
        )
        with patch("src.progena.agent.adapters.CheckpointChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "medium"
        assert kwargs["extra_body"] == {
            "reasoning": {"effort": "medium", "summary": "detailed"},
        }
        assert "reasoning" not in kwargs

    def test_reasoning_defaults_to_low_effort(self):
        """Reasoning defaults to low effort for standard models."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-chat-latest",
            reasoning_enabled=True,
        )
        with patch("src.progena.agent.adapters.CheckpointChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["extra_body"] == {
            "reasoning": {"effort": "low", "summary": "detailed"},
        }

    def test_reasoning_effort_respects_config_for_gpt_5_3_chat(self):
        """gpt-5.3-chat uses the configured reasoning effort."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-5.3-chat-2026-03-03",
            reasoning_enabled=True,
            reasoning_effort="medium",
        )
        with patch("src.progena.agent.adapters.CheckpointChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "medium"
        assert kwargs["extra_body"] == {
            "reasoning": {"effort": "medium", "summary": "detailed"},
        }

    def test_reasoning_summary_defaults_to_detailed(self):
        """Reasoning summary defaults to detailed when not overridden."""
        config = LLMConfig(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test-key",
            model_default="gpt-chat-latest",
            reasoning_enabled=True,
        )
        with patch("src.progena.agent.adapters.CheckpointChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["extra_body"] == {
            "reasoning": {"effort": "low", "summary": "detailed"},
        }

    def test_private_generation_hooks_preserve_bound_tools_and_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generation hooks unwrap bindings without dropping their model arguments."""
        boundary = _GenerationBoundaryModel()
        _install_generation_boundary(monkeypatch, boundary)
        tool = {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a value.",
                "parameters": {"type": "object"},
            },
        }
        model = ProviderChatModel().bind_tools(
            [tool],
            tool_choice="required",
            parallel_tool_calls=False,
        )
        messages = [HumanMessage(content="find the value")]

        result = model._generate(messages, stop=["stop"], request_marker="generate")
        chunks = list(model._stream(messages, request_marker="stream"))

        assert isinstance(result, ChatResult)
        assert result.generations[0].message.content == "raw"
        assert [chunk.message.content for chunk in chunks] == ["chunk"]
        generate_kwargs = boundary.calls[0][1]
        assert generate_kwargs["stop"] == ["stop"]
        assert generate_kwargs["tools"] == [tool]
        assert generate_kwargs["tool_choice"] == "required"
        assert generate_kwargs["parallel_tool_calls"] is False
        assert generate_kwargs["request_marker"] == "generate"
        stream_kwargs = boundary.calls[1][1]
        assert stream_kwargs["tools"] == [tool]
        assert stream_kwargs["tool_choice"] == "required"
        assert stream_kwargs["parallel_tool_calls"] is False
        assert stream_kwargs["request_marker"] == "stream"

    async def test_async_private_generation_hooks_preserve_bound_tools_and_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Async generation and streaming apply the same runnable binding settings."""
        boundary = _GenerationBoundaryModel()
        _install_generation_boundary(monkeypatch, boundary)
        model = ProviderChatModel().bind_tools(
            [{"type": "function", "function": {"name": "lookup"}}],
            tool_choice="lookup",
            strict=True,
        )
        messages = [HumanMessage(content="find the value")]

        result = await model._agenerate(messages, request_marker="agenerate")
        chunks = [chunk async for chunk in model._astream(messages, request_marker="astream")]

        assert isinstance(result, ChatResult)
        assert [chunk.message.content for chunk in chunks] == ["chunk"]
        generate_kwargs = boundary.calls[0][1]
        assert generate_kwargs["tools"] == [{"type": "function", "function": {"name": "lookup"}}]
        assert generate_kwargs["tool_choice"] == "lookup"
        assert generate_kwargs["strict"] is True
        assert generate_kwargs["request_marker"] == "agenerate"
        stream_kwargs = boundary.calls[1][1]
        assert stream_kwargs["tools"] == generate_kwargs["tools"]
        assert stream_kwargs["tool_choice"] == "lookup"
        assert stream_kwargs["strict"] is True
        assert stream_kwargs["request_marker"] == "astream"

    async def test_structured_output_keeps_parsing_and_generation_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Structured public calls parse output while private hooks return ChatResult."""
        boundary = _GenerationBoundaryModel()
        _install_generation_boundary(monkeypatch, boundary)
        schema = {
            "name": "answer",
            "description": "A structured answer.",
            "parameters": {"type": "object"},
        }
        model = ProviderChatModel().with_structured_output(
            schema,
            include_raw=True,
            method="json_schema",
        )
        messages = [HumanMessage(content="answer the question")]

        output = await model.ainvoke(messages)
        result = await model._agenerate(messages, request_marker="structured")

        assert output["parsed"] == {"answer": "raw"}
        assert output["raw"].content == "raw"
        assert output["parsing_error"] is None
        assert isinstance(result, ChatResult)
        assert result.generations[0].message.content == "raw"
        generation_kwargs = boundary.calls[1][1]
        assert generation_kwargs["response_format"] is schema
        assert generation_kwargs["method"] == "json_schema"
        assert generation_kwargs["request_marker"] == "structured"
