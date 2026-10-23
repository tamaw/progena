"""Tests for LLM client helpers and message handling."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import src.progena.llm.client as llm_client_module
from src.progena.config import LLMConfig, MCPConfig
from src.progena.llm.client import ChatGPTResponseStreamError, LLMClient
from src.progena.llm.schema import _build_json_schema_format_from_dict
from src.progena.llm.transport import _build_responses_endpoint
from src.progena.mcp.runtime import MCPRuntime


def _extract(data: dict):
    client = LLMClient.__new__(LLMClient)
    return LLMClient._extract_text_and_reasoning(client, data)


def _client_with_runtime() -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client.mcp_config = MCPConfig(servers=[])
    client._mcp_runtime = MCPRuntime(client.mcp_config)
    return client


def test_build_json_schema_format_from_dict_closes_objects_without_mutation():
    source = {
        "type": "object",
        "properties": {"timezone": {"type": "string"}},
    }
    fmt = _build_json_schema_format_from_dict(source, "preferences")

    assert fmt["type"] == "json_schema"
    inner = fmt["json_schema"]
    assert inner["name"] == "preferences"
    assert inner["strict"] is True
    # Strict object schema enforcement is applied to the copy...
    assert inner["schema"]["additionalProperties"] is False
    assert inner["schema"]["required"] == ["timezone"]
    # ...and the caller's dict is left untouched.
    assert "additionalProperties" not in source


def test_build_json_schema_format_from_dict_preserves_dict_additional_properties():
    # A map-typed field's "additionalProperties" is itself a value schema
    # (e.g. dict[str, OneOfNodeShape]), not a boolean - closing must recurse
    # into it rather than clobbering it with False, which would leave the
    # map unable to hold any properties at all (self-contradictory schema).
    source = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "object",
                "minProperties": 1,
                "additionalProperties": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"}},
                },
            }
        },
    }
    fmt = _build_json_schema_format_from_dict(source, "graph")
    nodes_schema = fmt["json_schema"]["schema"]["properties"]["nodes"]
    node_value_schema = nodes_schema["additionalProperties"]

    assert isinstance(node_value_schema, dict)
    assert node_value_schema["type"] == "object"
    # The recursed-into value schema is itself closed.
    assert node_value_schema["additionalProperties"] is False
    assert node_value_schema["required"] == ["kind"]


def test_build_json_schema_format_from_dict_defaults_missing_root_type():
    # Plain JSON Schema treats a root schema with only properties/required as
    # implicitly object-shaped, but the provider requires an explicit
    # top-level "type": "object" even in non-strict mode.
    source = {"properties": {"foo": {"type": "string"}}, "required": ["foo"]}
    fmt = _build_json_schema_format_from_dict(source, "ad_hoc", strict=False)
    assert fmt["json_schema"]["schema"]["type"] == "object"


def test_llm_client_configures_connect_retries():
    # The custom LLMClient's httpx client is idle for most of a turn (only
    # used by structured_output/a2a_send, unlike the main chat loop which
    # goes through a separate LangChain client) - past the default 5s
    # keepalive expiry, its next request opens a fresh connection, which in
    # this environment intermittently fails DNS resolution once before
    # succeeding. Without transport-level retries that surfaces as a hard
    # tool failure instead of being absorbed transparently.
    #
    # Assert via the constructor calls rather than the real client's private
    # httpx internals (`_transport`/`_pool._retries`), which are undocumented
    # and can shift between httpx versions; this also avoids leaving a real,
    # unclosed httpx.AsyncClient behind.
    with (
        patch("src.progena.llm.client.httpx.AsyncHTTPTransport") as mock_transport_cls,
        patch("src.progena.llm.client.httpx.AsyncClient") as mock_client_cls,
    ):
        LLMClient(config=LLMConfig(base_url="https://example.test/openai/v1"))

    mock_transport_cls.assert_called_once_with(retries=2)
    _, kwargs = mock_client_cls.call_args
    assert kwargs["transport"] is mock_transport_cls.return_value


@pytest.mark.asyncio
async def test_chat_structured_json_parses_and_forwards_schema():
    client = LLMClient.__new__(LLMClient)
    client.chat = AsyncMock(return_value='{"timezone": "UTC"}')

    schema = {"type": "object", "properties": {"timezone": {"type": "string"}}}
    result = await LLMClient.chat_structured_json(
        client,
        prompt="set timezone",
        json_schema=schema,
        schema_name="preferences",
        system_prompt="be precise",
    )

    assert result == {"timezone": "UTC"}
    _, kwargs = client.chat.call_args
    assert kwargs["response_format"]["json_schema"]["name"] == "preferences"
    assert kwargs["system_prompt"] == "be precise"


def test_build_responses_endpoint_from_base_url():
    endpoint = _build_responses_endpoint("https://example.openai.azure.com/openai/v1")
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses"


def test_build_responses_endpoint_keeps_existing_responses_path_and_query():
    endpoint = _build_responses_endpoint(
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )
    assert endpoint == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"


def test_extracts_top_level_text_string():
    text, reasoning = _extract({"text": "Hello from top-level text"})
    assert text == "Hello from top-level text"
    assert reasoning == ""


def test_extracts_top_level_text_dict():
    text, reasoning = _extract({"text": {"text": "Hello from dict text"}})
    assert text == "Hello from dict text"
    assert reasoning == ""


def test_extracts_top_level_text_list_parts():
    text, reasoning = _extract({"text": ["Hello ", {"text": "world"}]})
    assert text == "Hello world"
    assert reasoning == ""


def test_extracts_top_level_text_nested_value_list():
    payload = {
        "text": {
            "value": [
                {"type": "output_text", "text": "Hello "},
                {"type": "output_text", "text": {"value": "nested"}},
            ]
        }
    }
    text, reasoning = _extract(payload)
    assert text == "Hello nested"
    assert reasoning == ""


def test_extracts_output_message_part_with_nested_text_dict():
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": {"value": "From output content"}}],
            }
        ]
    }
    text, reasoning = _extract(payload)
    assert text == "From output content"
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_top_level_text():
    payload = {
        "text": 'schedule_task{"description":"Foot tapping reminder","delay_minutes":2}✅ Scheduled.'
    }
    text, reasoning = _extract(payload)
    assert text == "✅ Scheduled."
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_output_text():
    payload = {
        "output_text": 'schedule_task{"instruction":"Remind user"}In two minutes, reminder set.'
    }
    text, _ = _extract(payload)
    assert text == "In two minutes, reminder set."


def test_extract_tool_calls_supports_function_call_items():
    client = LLMClient.__new__(LLMClient)
    payload = {
        "output": [
            {
                "type": "function_call",
                "name": "current_time",
                "call_id": "call_123",
                "arguments": "{}",
            }
        ]
    }

    tool_calls = LLMClient._extract_tool_calls(client, payload)
    assert len(tool_calls) == 1
    assert tool_calls[0]["type"] == "function_call"
    assert tool_calls[0]["name"] == "current_time"


def test_extract_tool_calls_supports_function_call_in_message_content():
    client = LLMClient.__new__(LLMClient)
    payload = {
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "function_call",
                        "name": "current_time",
                        "call_id": "call_abc",
                        "arguments": "{}",
                    }
                ],
            }
        ]
    }

    tool_calls = LLMClient._extract_tool_calls(client, payload)
    assert len(tool_calls) == 1
    assert tool_calls[0]["call_id"] == "call_abc"


def test_merge_tools_respects_explicit_empty_list():
    client = _client_with_runtime()
    client.mcp_runtime.set_context(
        {},
        [
            {
                "name": "primer",
                "description": "Primer tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    merged = LLMClient._merge_tools(client, [])
    assert merged == []


def test_resolve_request_tools_includes_tools_for_structured_by_default():
    client = _client_with_runtime()
    client.mcp_runtime.set_context(
        {},
        [
            {
                "name": "primer",
                "description": "Primer tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    resolved = LLMClient._resolve_request_tools(
        client,
        tools=None,
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
    )
    assert isinstance(resolved, list)
    # Responses API tools are flat ({"type": "function", "name": ...}), not
    # nested under "function" like the older Chat Completions shape --
    # nesting them is what produced "Missing required parameter:
    # 'tools[0].name'" from Azure whenever an MCP tool was registered.
    assert any(t.get("name") == "primer" for t in resolved)


def test_resolve_request_tools_keeps_tools_for_non_structured_requests():
    client = _client_with_runtime()
    client.mcp_runtime.set_context(
        {},
        [
            {
                "name": "primer",
                "description": "Primer tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    resolved = LLMClient._resolve_request_tools(
        client,
        tools=None,
        response_format=None,
    )
    assert isinstance(resolved, list)
    # Responses API tools are flat ({"type": "function", "name": ...}), not
    # nested under "function" like the older Chat Completions shape --
    # nesting them is what produced "Missing required parameter:
    # 'tools[0].name'" from Azure whenever an MCP tool was registered.
    assert any(t.get("name") == "primer" for t in resolved)


@pytest.mark.asyncio
async def test_execute_tool_call_routes_url_only_registry_entry_to_a2a():
    client = LLMClient.__new__(LLMClient)
    client._call_mcp_tool = AsyncMock(return_value={"error": "should-not-call-mcp"})
    tool_registry = SimpleNamespace(
        find_by_name=lambda _name: {
            "name": "builder",
            "url": "http://external-agent:8100",
        }
    )

    with patch(
        "src.progena.tools.system_registry.execute_system_tool",
        new=AsyncMock(
            return_value=(
                {
                    "task_id": "task-123",
                    "agent": "builder",
                },
                None,
                None,
                False,
            )
        ),
    ) as mock_execute:
        result = await LLMClient._execute_tool_call(
            client,
            {"id": "tool_1"},
            "builder",
            {"request": "hello", "context": {"caller": "test"}},
            {"tool_registry": tool_registry, "state_manager": MagicMock()},
        )

    assert result["result"]["task_id"] == "task-123"
    client._call_mcp_tool.assert_not_awaited()
    mock_execute.assert_awaited_once()
    assert mock_execute.await_args.kwargs["tool_name"] == "a2a_send"
    assert mock_execute.await_args.kwargs["tool_args"]["agent"] == "builder"


@pytest.mark.asyncio
async def test_call_mcp_tool_parses_sse_result():
    client = _client_with_runtime()
    client.mcp_config = SimpleNamespace(enabled_servers=[SimpleNamespace(name="jina")])
    tool_lookup = {
        "jina_read_url": {
            "name": "jina_read_url",
            "mcp_original_name": "read_url",
            "mcp_server_name": "jina",
        }
    }
    client.mcp_runtime.mcp_tool_lookup = dict(tool_lookup)
    client.mcp_runtime.mcp_tools = list(tool_lookup.values())
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = 'event: message\ndata: {"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    mcp_client = AsyncMock()
    mcp_client.post.return_value = response
    client.mcp_runtime.mcp_servers = {
        "jina": {
            "client": mcp_client,
            "url": "https://mcp.jina.ai/v1",
        }
    }

    result = await LLMClient._call_mcp_tool(
        client,
        {
            "name": "jina_read_url",
            "arguments": {"url": "https://example.com"},
            "call_id": "call-1",
        },
    )

    assert result == {"content": [{"type": "text", "text": "ok"}]}
    mcp_client.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_call_mcp_tool_routes_to_server_by_alias():
    client = _client_with_runtime()
    client.mcp_config = SimpleNamespace(enabled_servers=[SimpleNamespace(name="jina")])
    tool_lookup = {
        "jina_search_web": {
            "name": "jina_search_web",
            "mcp_original_name": "search_web",
            "mcp_server_name": "jina",
        }
    }
    client.mcp_runtime.mcp_tool_lookup = dict(tool_lookup)
    client.mcp_runtime.mcp_tools = list(tool_lookup.values())
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = '{"result":{"items":["ok"]}}'
    response.json.return_value = {"result": {"items": ["ok"]}}
    mcp_client = AsyncMock()
    mcp_client.post.return_value = response
    client.mcp_runtime.mcp_servers = {
        "jina": {
            "client": mcp_client,
            "url": "https://mcp.jina.ai/v1",
        }
    }

    result = await LLMClient._call_mcp_tool(
        client,
        {
            "name": "jina_search_web",
            "arguments": {"query": "latest ai"},
            "call_id": "call-1",
        },
    )

    assert result == {"items": ["ok"]}
    mcp_client.post.assert_awaited_once()
    assert mcp_client.post.await_args.args[0] == "https://mcp.jina.ai/v1"
    payload = mcp_client.post.await_args.kwargs["json"]
    assert payload["params"]["name"] == "search_web"
    assert payload["params"]["arguments"] == {"query": "latest ai"}


@pytest.mark.asyncio
async def test_chatgpt_auxiliary_tool_round_uses_stateless_sse_history():
    requests: list[dict] = []

    responses = [
        {
            "output": [
                {
                    "type": "reasoning",
                    "id": "rsn_1",
                    "summary": [],
                    "encrypted_content": "opaque-reasoning",
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "sample",
                    "namespace": "progena",
                    "arguments": '{"value": 1}',
                },
            ]
        },
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        },
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = {
            "type": "response.completed",
            "response": responses[len(requests) - 1],
        }
        return httpx.Response(
            200,
            text=f"event: response.completed\ndata: {json.dumps(body)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = LLMClient.__new__(LLMClient)
    client._mcp_runtime = None
    client._event_pusher = None
    client._responses_connection = AsyncMock(
        return_value=(
            SimpleNamespace(
                provider_kind="chatgpt",
                model_default="gpt-test",
                reasoning_enabled=False,
            ),
            "https://api.openai.com/v1/responses",
            {"Authorization": "Bearer oauth-token"},
        )
    )
    client._execute_tool_call = AsyncMock(return_value={"result": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client.client = transport
        result = await client._post_response(
            [
                {"role": "system", "content": "Follow the tool contract."},
                {"role": "user", "content": "Run sample."},
            ],
            temperature=0.1,
            max_tokens=9,
            tools=[
                {
                    "type": "function",
                    "name": "sample",
                    "description": "Sample tool",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        )

    assert result == responses[1]
    assert len(requests) == 2
    assert requests[0]["store"] is False
    assert requests[0]["stream"] is True
    assert requests[0]["include"] == ["reasoning.encrypted_content"]
    assert requests[0]["input"][0]["role"] == "developer"
    assert {"max_output_tokens", "temperature", "previous_response_id"}.isdisjoint(requests[0])
    assert requests[0]["tools"] == [
        {
            "type": "namespace",
            "name": "progena",
            "description": "Progena local system and MCP tools.",
            "tools": [
                {
                    "type": "function",
                    "name": "sample",
                    "description": "Sample tool",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        }
    ]
    assert requests[1]["input"][2:5] == [
        responses[0]["output"][0],
        responses[0]["output"][1],
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": '{"result": "ok"}',
            "namespace": "progena",
        },
    ]
    assert client._execute_tool_call.await_args.kwargs["tool_name"] == "sample"


@pytest.mark.asyncio
async def test_chatgpt_auxiliary_request_retries_unauthorized_before_processing_output(monkeypatch):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        body = {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "retried"}],
                    }
                ]
            },
        }
        return httpx.Response(
            200,
            text=f"event: response.completed\ndata: {json.dumps(body)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = LLMClient.__new__(LLMClient)
    client._mcp_runtime = None
    client._event_pusher = None
    client._responses_connection = AsyncMock(
        return_value=(
            SimpleNamespace(
                provider_kind="chatgpt",
                provider_id="provider-1",
                model_default="gpt-test",
                reasoning_enabled=False,
            ),
            "https://api.openai.com/v1/responses",
            {"Authorization": "Bearer rejected-token"},
        )
    )
    refresh = AsyncMock(return_value="rotated-token")
    monkeypatch.setattr(llm_client_module, "refresh_chatgpt_access_token_after_rejection", refresh)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client.client = transport
        result = await client._post_response([{"role": "user", "content": "Retry."}])

    assert result["output"][0]["content"][0]["text"] == "retried"
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer rejected-token",
        "Bearer rotated-token",
    ]
    refresh.assert_awaited_once_with("provider-1", "rejected-token")


@pytest.mark.asyncio
async def test_chatgpt_tool_followup_retries_without_reexecuting_tool(monkeypatch):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            response = {
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "sample",
                        "namespace": "progena",
                        "arguments": "{}",
                    }
                ]
            }
            return httpx.Response(
                200,
                text=(
                    "event: response.completed\n"
                    f"data: {json.dumps({'type': 'response.completed', 'response': response})}\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        if len(requests) == 2:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        response = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                }
            ]
        }
        return httpx.Response(
            200,
            text=(
                "event: response.completed\n"
                f"data: {json.dumps({'type': 'response.completed', 'response': response})}\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = LLMClient.__new__(LLMClient)
    client._mcp_runtime = None
    client._event_pusher = None
    client._responses_connection = AsyncMock(
        return_value=(
            SimpleNamespace(
                provider_kind="chatgpt",
                provider_id="provider-1",
                model_default="gpt-test",
                reasoning_enabled=False,
            ),
            "https://api.openai.com/v1/responses",
            {"Authorization": "Bearer rejected-token"},
        )
    )
    client._execute_tool_call = AsyncMock(return_value={"result": "ok"})
    refresh = AsyncMock(return_value="rotated-token")
    monkeypatch.setattr(llm_client_module, "refresh_chatgpt_access_token_after_rejection", refresh)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client.client = transport
        result = await client._post_response(
            [{"role": "user", "content": "Run sample."}],
            tools=[
                {
                    "type": "function",
                    "name": "sample",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        )

    assert result["output"][0]["content"][0]["text"] == "done"
    assert client._execute_tool_call.await_count == 1
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer rejected-token",
        "Bearer rejected-token",
        "Bearer rotated-token",
    ]
    refresh.assert_awaited_once_with("provider-1", "rejected-token")


@pytest.mark.asyncio
async def test_chatgpt_auxiliary_stream_rejects_incomplete_completion():
    def respond(_request: httpx.Request) -> httpx.Response:
        body = {
            "type": "response.incomplete",
            "response": {"incomplete_details": {"reason": "max_output_tokens"}},
        }
        return httpx.Response(
            200,
            text=f"event: response.incomplete\ndata: {json.dumps(body)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    client = LLMClient.__new__(LLMClient)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client.client = transport
        with pytest.raises(ChatGPTResponseStreamError) as exc_info:
            await client._post_chatgpt_stream(
                "https://api.openai.com/v1/responses",
                {"model": "gpt-test", "input": [], "store": False, "stream": True},
                {"Authorization": "Bearer oauth-token"},
            )

    assert exc_info.value.event_type == "response.incomplete"
    assert exc_info.value.detail == "max_output_tokens"


def test_chatgpt_custom_tool_calls_are_extracted_for_execution():
    client = LLMClient.__new__(LLMClient)
    assert client._extract_tool_calls(
        {
            "output": [
                {
                    "type": "custom_tool_call",
                    "call_id": "call_custom",
                    "name": "progena_command",
                    "input": "status",
                }
            ]
        }
    ) == [
        {
            "type": "custom_tool_call",
            "call_id": "call_custom",
            "name": "progena_command",
            "input": "status",
        }
    ]
