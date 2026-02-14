"""Tests for LLM response text extraction edge cases."""

from app.progenitor.llm.client import LLMClient


def _extract(data: dict):
    client = LLMClient.__new__(LLMClient)
    return LLMClient._extract_text_and_reasoning(client, data)


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


def test_resolve_request_tools_disables_tools_for_structured_by_default():
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
    assert resolved == []


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
