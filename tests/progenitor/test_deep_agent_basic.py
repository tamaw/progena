"""
Tests for Phase 1: DeepAgent foundation — LangChain adapter and agent factory.
"""

from unittest.mock import MagicMock, patch

from app.progenitor.agent.adapters import create_langchain_model
from app.progenitor.config import LLMConfig


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
        with patch("app.progenitor.agent.adapters.ChatOpenAI") as mock_chat_openai:
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
        with patch("app.progenitor.agent.adapters.ChatOpenAI") as mock_chat_openai:
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
        with patch("app.progenitor.agent.adapters.ChatOpenAI") as mock_chat_openai:
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
        with patch("app.progenitor.agent.adapters.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = MagicMock()
            create_langchain_model(config)

        _, kwargs = mock_chat_openai.call_args
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["extra_body"] == {
            "reasoning": {"effort": "low", "summary": "detailed"},
        }


class TestSchemaCompaction:
    """Tests for tool-schema compaction (SchemaCompactionMiddleware / _compact_args_model).

    Two distinct ``tool.args_schema`` shapes flow through here: a Pydantic
    model class (system tools, DeepAgent built-ins) and a raw JSON-Schema
    dict (every MCP tool -- ``langchain_mcp_adapters`` wraps ``args_schema``
    as the server's ``inputSchema`` dict verbatim, with no Python model
    class to subclass).
    """

    def test_compacts_pydantic_model_class_and_strips_noise(self):
        from pydantic import BaseModel, Field

        from app.progenitor.agent.middleware.schema_compaction import _compact_args_model

        class _Args(BaseModel):
            query: str = Field(description="search query")
            limit: int | None = None

        compacted = _compact_args_model(_Args, {})

        assert compacted is not _Args
        assert issubclass(compacted, _Args)
        schema = compacted.model_json_schema()
        assert "title" not in schema
        assert "$schema" not in schema
        # Optional[int] flattens from anyOf([{type:integer},{type:null}]) to
        # a plain type union -- same field still just accepts int | None.
        assert schema["properties"]["limit"]["type"] == ["integer", "null"]
        # The model still works normally: validation is untouched.
        instance = compacted(query="hi")
        assert instance.query == "hi"
        assert instance.limit is None

    def test_pydantic_model_class_compaction_is_idempotent_via_cache(self):
        from pydantic import BaseModel

        from app.progenitor.agent.middleware.schema_compaction import _compact_args_model

        class _Args(BaseModel):
            query: str

        cache: dict[int, object] = {}
        first = _compact_args_model(_Args, cache)
        second = _compact_args_model(_Args, cache)
        assert first is second
        # An already-compacted class passed back through is returned unchanged.
        third = _compact_args_model(first, cache)
        assert third is first

    def test_compacts_raw_dict_schema_from_mcp_tools(self):
        from app.progenitor.agent.middleware.schema_compaction import _compact_args_model

        # Shape langchain_mcp_adapters produces from an MCP server's inputSchema.
        raw_schema = {
            "title": "search_webArguments",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "query": {"title": "Query", "type": "string"},
                "limit": {
                    "title": "Limit",
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                },
            },
            "required": ["query"],
        }

        compacted = _compact_args_model(raw_schema, {})

        assert "title" not in compacted
        assert "$schema" not in compacted
        assert compacted["properties"]["limit"]["type"] == ["integer", "null"]
        # The original dict (owned by the MCP tool wrapper) is never mutated.
        assert raw_schema["title"] == "search_webArguments"
        assert "anyOf" in raw_schema["properties"]["limit"]

    def test_dict_schema_compaction_does_not_re_compact_on_repeated_calls(self):
        """Regression: an already-compacted dict fed back in must be a cache
        hit, not deep-copied and re-walked again -- otherwise every heartbeat
        turn would grow an unbounded chain of throwaway dict copies for tools
        whose ``args_schema`` is a dict (this is exactly what happens on a
        real turn: ``tool.args_schema`` is mutated in place, so the *next*
        turn's ``getattr(tool, "args_schema")`` reads back the already-
        compacted dict, not the original)."""
        from app.progenitor.agent.middleware.schema_compaction import _compact_args_model

        raw_schema = {"title": "X", "type": "object", "properties": {}}
        cache: dict[int, object] = {}

        first = _compact_args_model(raw_schema, cache)
        # Simulate the middleware re-reading tool.args_schema on the next turn:
        # it's now `first`, not `raw_schema`.
        second = _compact_args_model(first, cache)
        third = _compact_args_model(second, cache)

        assert first is second is third

    def test_middleware_compacts_dict_args_schema_tool_in_place(self):
        from app.progenitor.agent.middleware.schema_compaction import SchemaCompactionMiddleware

        class _FakeMCPTool:
            """Mimics a langchain_mcp_adapters StructuredTool: args_schema is a dict."""

            def __init__(self) -> None:
                self.args_schema = {
                    "title": "search_webArguments",
                    "type": "object",
                    "properties": {"query": {"title": "Query", "type": "string"}},
                }

        tool = _FakeMCPTool()
        middleware = SchemaCompactionMiddleware()

        # Must not raise (this is the exact crash MCP tools hit before the fix:
        # dict(name, bases, namespace) - dicts aren't a valid metaclass call).
        middleware._compact_tool_in_place(tool)

        assert "title" not in tool.args_schema
        assert tool.args_schema["properties"]["query"] == {"type": "string"}

        # A second pass (next model call in the same turn, or the next turn)
        # must not re-wrap/re-copy the now-compacted schema.
        compacted_ref = tool.args_schema
        middleware._compact_tool_in_place(tool)
        assert tool.args_schema is compacted_ref


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


class TestDeepAgentFactory:
    """Tests for the DeepAgent factory."""

    async def test_create_agent_returns_compiled_graph(self):
        """Factory returns a compiled LangGraph state graph."""
        with (
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_model = MagicMock()
            mock_create_model.return_value = mock_model
            mock_graph = MagicMock()
            mock_create_deep_agent.return_value = mock_graph
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            config = ProgenitorConfig()
            result = await create_progenitor_agent(
                config=config, state_manager=FakeContentStateManager()
            )

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

    async def test_create_agent_uses_custom_checkpointer(self):
        """Custom checkpointer is passed through."""
        with (
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create_model.return_value = MagicMock()
            mock_create_deep_agent.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            custom_checkpointer = MagicMock()
            await create_progenitor_agent(
                config=ProgenitorConfig(),
                checkpointer=custom_checkpointer,
                state_manager=FakeContentStateManager(),
            )

            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["checkpointer"] is custom_checkpointer

    async def test_create_agent_uses_genesis_prompt(self):
        """DeepAgent factory uses the canonical genesis prompt."""
        with (
            patch("app.progenitor.agent.agent.create_langchain_model") as mock_create_model,
            patch("app.progenitor.agent.agent.create_deep_agent") as mock_create_deep_agent,
            patch("app.progenitor.agent.agent.create_progenitor_backend") as mock_backend,
        ):
            mock_create_model.return_value = MagicMock()
            mock_create_deep_agent.return_value = MagicMock()
            mock_backend.return_value = MagicMock()

            from app.progenitor.agent.agent import create_progenitor_agent
            from app.progenitor.config import ProgenitorConfig

            config = ProgenitorConfig()
            state_manager = FakeContentStateManager(prompt="GENESIS")
            await create_progenitor_agent(
                config=config,
                checkpointer=MagicMock(),
                state_manager=state_manager,
            )

            call_kwargs = mock_create_deep_agent.call_args
            assert call_kwargs.kwargs["system_prompt"] == "GENESIS"
