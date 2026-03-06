"""Tests for LLM client helpers and message handling."""

from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.progenitor.llm.client import (
    DECISION_HISTORY_LIMIT,
    RESPONSE_HISTORY_LIMIT,
    LLMClient,
    _build_responses_endpoint,
)


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
                "content": [
                    {"type": "output_text", "text": {"value": "From output content"}}
                ],
            }
        ]
    }
    text, reasoning = _extract(payload)
    assert text == "From output content"
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_top_level_text():
    payload = {
        "text": "schedule_task{\"description\":\"Foot tapping reminder\",\"delay_minutes\":2}✅ Scheduled."
    }
    text, reasoning = _extract(payload)
    assert text == "✅ Scheduled."
    assert reasoning == ""


def test_extract_strips_inline_tool_markup_from_output_text():
    payload = {
        "output_text": "schedule_task{\"instruction\":\"Remind user\"}In two minutes, reminder set."
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
async def test_decide_trims_history_to_recent_messages():
    client = LLMClient.__new__(LLMClient)
    captured: dict[str, object] = {}
    client.prompts = SimpleNamespace(get_decide_prompt=lambda _: "decide prompt")

    async def fake_chat(self, messages, system_prompt=None, temperature=0.7, max_tokens=4096, tools=None, response_format=None):
        captured["messages"] = messages
        captured["system_prompt"] = system_prompt
        return '{"action":"answer_directly","confidence":0.9,"reasoning":"ok","requires_planning":false}'

    client.chat = MethodType(fake_chat, client)
    history = [{"role": "user", "content": f"message-{i}"} for i in range(10)]

    await LLMClient.decide(client, "latest-input", history, "tools")

    sent_messages = captured["messages"]
    assert isinstance(sent_messages, list)
    assert [msg["content"] for msg in sent_messages] == [
        *(f"message-{i}" for i in range(10 - DECISION_HISTORY_LIMIT, 10)),
        "latest-input",
    ]


@pytest.mark.asyncio
async def test_generate_response_trims_history_to_recent_messages():
    client = LLMClient.__new__(LLMClient)
    captured: dict[str, object] = {}
    client.prompts = SimpleNamespace(get_genesis_prompt=lambda: "genesis")

    async def fake_chat(self, messages, system_prompt=None, temperature=0.7, max_tokens=4096, tools=None, response_format=None):
        captured["messages"] = messages
        captured["system_prompt"] = system_prompt
        return "ok"

    client.chat = MethodType(fake_chat, client)
    history = [{"role": "assistant", "content": f"message-{i}"} for i in range(20)]

    result = await LLMClient.generate_response(client, "latest-input", history)

    assert result == "ok"
    sent_messages = captured["messages"]
    assert isinstance(sent_messages, list)
    assert [msg["content"] for msg in sent_messages] == [
        *(f"message-{i}" for i in range(20 - RESPONSE_HISTORY_LIMIT, 20)),
        "latest-input",
    ]


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
        "app.progenitor.tools.mcp.A2AClient.call_agent",
        new=AsyncMock(return_value={"success": True, "result": {"ok": True}}),
    ) as mock_call_agent:
        result = await LLMClient._execute_tool_call(
            client,
            {"id": "tool_1"},
            "builder",
            {"request": "hello", "context": {"caller": "test"}},
            {"tool_registry": tool_registry},
        )

    assert result == {"result": {"success": True, "result": {"ok": True}}}
    client._call_mcp_tool.assert_not_awaited()
    mock_call_agent.assert_awaited_once()
