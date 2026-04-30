from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.tools import StructuredTool

from app.progenitor.tools.mcp import initialize_mcp_langchain_tools


def _adapter_tool(name: str) -> StructuredTool:
    async def _run(query: str = "") -> dict[str, str]:
        return {"query": query}

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=f"Tool: {name}",
    )


@pytest.mark.asyncio
async def test_initialize_mcp_langchain_tools_uses_adapter_prefixing() -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, connections, *, tool_name_prefix=False):
            captured["connections"] = connections
            captured["tool_name_prefix"] = tool_name_prefix

        async def get_tools(self, *, server_name=None):
            assert server_name == "jina"
            return [_adapter_tool("jina_search_web")]

    config = SimpleNamespace(
        enabled_servers=[
            SimpleNamespace(
                name="jina",
                transport="http",
                url="https://mcp.jina.ai/v1",
                headers={"Authorization": "Bearer test"},
            )
        ]
    )
    with patch("app.progenitor.tools.mcp.MultiServerMCPClient", FakeClient):
        tools, entries = await initialize_mcp_langchain_tools(config)

    assert captured["connections"] == {
        "jina": {
            "transport": "streamable_http",
            "url": "https://mcp.jina.ai/v1",
            "headers": {"Authorization": "Bearer test"},
        }
    }
    assert captured["tool_name_prefix"] is True
    assert [tool.name for tool in tools] == ["jina_search_web"]
    assert entries == [
        {
            "name": "jina_search_web",
            "description": "Tool: jina_search_web",
            "type": "mcp_tool",
            "source": "jina",
            "endpoint": "https://mcp.jina.ai/v1",
            "transport": "http",
            "capabilities": ["jina_search_web"],
            "server_name": "jina",
            "original_name": "search_web",
        }
    ]
    assert tools[0].metadata == {
        "mcp_server_name": "jina",
        "mcp_server_url": "https://mcp.jina.ai/v1",
        "mcp_original_name": "search_web",
    }
    assert await tools[0].ainvoke({"query": "langgraph"}) == {"query": "langgraph"}
