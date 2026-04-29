"""Tests for LLM client helpers and message handling."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.progenitor.llm.client import LLMClient, _build_responses_endpoint


def _extract(data: dict):
    client = LLMClient.__new__(LLMClient)
    return LLMClient._extract_text_and_reasoning(client, data)


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
    client = LLMClient.__new__(LLMClient)
    client.mcp_tools = [
        {
            "name": "primer",
            "description": "Primer tool",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    merged = LLMClient._merge_tools(client, [])
    assert merged == []


def test_resolve_request_tools_includes_tools_for_structured_by_default():
    client = LLMClient.__new__(LLMClient)
    client.mcp_tools = [
        {
            "name": "primer",
            "description": "Primer tool",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    resolved = LLMClient._resolve_request_tools(
        client,
        tools=None,
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {}}},
    )
    assert isinstance(resolved, list)
    assert any(t.get("function", {}).get("name") == "primer" for t in resolved)


def test_resolve_request_tools_keeps_tools_for_non_structured_requests():
    client = LLMClient.__new__(LLMClient)
    client.mcp_tools = [
        {
            "name": "primer",
            "description": "Primer tool",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    resolved = LLMClient._resolve_request_tools(
        client,
        tools=None,
        response_format=None,
    )
    assert isinstance(resolved, list)
    assert any(t.get("function", {}).get("name") == "primer" for t in resolved)


@pytest.mark.asyncio
async def test_execute_tool_call_routes_url_only_registry_entry_to_a2a():
    client = LLMClient.__new__(LLMClient)
    client._call_mcp_tool = AsyncMock(return_value={"error": "should-not-call-mcp"})
    tool_registry = SimpleNamespace(
        find_by_name=lambda _name: {
            "name": "builder",
            "url": "http://progenitor-agent-builder:8108",
        }
    )

    with patch(
        "app.progenitor.tools.system_registry.execute_system_tool",
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
    assert mock_execute.await_args.kwargs["tool_name"] == "call_agent"
    assert mock_execute.await_args.kwargs["tool_args"]["agent"] == "builder"


@pytest.mark.asyncio
async def test_call_mcp_tool_parses_sse_result():
    client = LLMClient.__new__(LLMClient)
    client.mcp_config = SimpleNamespace(enabled_servers=[SimpleNamespace(name="jina")])
    client.mcp_tool_lookup = {
        "jina_read_url": {
            "name": "jina_read_url",
            "mcp_original_name": "read_url",
            "mcp_server_name": "jina",
        }
    }
    client.mcp_tools = list(client.mcp_tool_lookup.values())
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = 'event: message\ndata: {"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    mcp_client = AsyncMock()
    mcp_client.post.return_value = response
    client.mcp_servers = {
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
    client = LLMClient.__new__(LLMClient)
    client.mcp_config = SimpleNamespace(enabled_servers=[SimpleNamespace(name="jina")])
    client.mcp_tool_lookup = {
        "jina_search_web": {
            "name": "jina_search_web",
            "mcp_original_name": "search_web",
            "mcp_server_name": "jina",
        }
    }
    client.mcp_tools = list(client.mcp_tool_lookup.values())
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = '{"result":{"items":["ok"]}}'
    response.json.return_value = {"result": {"items": ["ok"]}}
    mcp_client = AsyncMock()
    mcp_client.post.return_value = response
    client.mcp_servers = {
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
