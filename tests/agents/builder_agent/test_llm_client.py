"""Tests for builder_agent schema formatting and MCP helpers."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[3] / "data"))

from app.agents.builder_agent.llm.client import LLMClient, _build_json_schema_format
from app.agents.builder_agent.nodes.think import ThinkResponse
from app.agents.builder_agent.state.ideal_state import IdealState


def test_schema_format_closes_nested_object_defs():
    response_format = _build_json_schema_format(ThinkResponse, "think_response")
    schema = response_format["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "approaches",
        "recommended_approach_index",
        "recommendation_reasoning",
        "reasoning_trace",
    }
    assert schema["$defs"]["ThinkApproachResponse"]["additionalProperties"] is False
    assert set(schema["$defs"]["ThinkApproachResponse"]["required"]) == {
        "name",
        "strategy",
        "implementation_steps",
        "trade_offs",
        "risks",
        "expected_success_probability",
        "addresses_gaps",
    }
    assert schema["$defs"]["ThinkTradeOffResponse"]["additionalProperties"] is False
    assert schema["$defs"]["ThinkRiskResponse"]["additionalProperties"] is False


def test_schema_format_strips_ref_siblings_for_azure():
    response_format = _build_json_schema_format(IdealState, "ideal_state")
    schema = response_format["json_schema"]["schema"]
    criterion_type = schema["$defs"]["SuccessCriterion"]["properties"]["criterion_type"]

    assert criterion_type == {"$ref": "#/$defs/CriterionType"}


@pytest.mark.asyncio
async def test_call_mcp_tool_parses_sse_result():
    client = LLMClient.__new__(LLMClient)
    client.mcp_config = SimpleNamespace(base_url="https://mcp.jina.ai/v1", enabled=True)
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = 'event: message\ndata: {"result":{"content":[{"type":"text","text":"ok"}]}}\n\n'
    response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
    client.mcp_client = AsyncMock()
    client.mcp_client.post.return_value = response

    result = await LLMClient._call_mcp_tool(
        client,
        {
            "name": "read_url",
            "arguments": {"url": "https://example.com"},
            "call_id": "call-1",
        },
    )

    assert result == {"content": [{"type": "text", "text": "ok"}]}
    client.mcp_client.post.assert_awaited_once_with(
        "https://mcp.jina.ai/v1",
        json={
            "jsonrpc": "2.0",
            "id": "call-1",
            "method": "tools/call",
            "params": {
                "name": "read_url",
                "arguments": {"url": "https://example.com"},
            },
        },
    )
