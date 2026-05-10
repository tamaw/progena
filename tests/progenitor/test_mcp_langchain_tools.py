from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.tools import StructuredTool

from app.progenitor.mcp import build_mcp_langchain_tool_wrappers


def _adapter_tool(name: str) -> StructuredTool:
    async def _run(query: str = "") -> dict[str, str]:
        return {"query": query}

    return StructuredTool.from_function(
        coroutine=_run,
        name=name,
        description=f"Tool: {name}",
    )


@pytest.mark.asyncio
async def test_build_mcp_langchain_tool_wrappers_applies_runtime_aliases() -> None:
    captured_connections: dict[str, dict[str, dict[str, str]]] = {}

    class FakeClient:
        def __init__(self, connections):
            captured_connections["value"] = connections

        async def get_tools(self, *, server_name=None):
            assert server_name == "jina"
            return [_adapter_tool("search_web")]

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
    mcp_tools = [
        {
            "name": "jina_search_web",
            "description": "Search the web [server: jina, original: search_web]",
            "mcp_original_name": "search_web",
            "mcp_server_name": "jina",
        }
    ]

    with patch("app.progenitor.mcp.context.MultiServerMCPClient", FakeClient):
        tools = await build_mcp_langchain_tool_wrappers(config, mcp_tools)

    assert captured_connections["value"] == {
        "jina": {
            "transport": "streamable_http",
            "url": "https://mcp.jina.ai/v1",
            "headers": {"Authorization": "Bearer test"},
        }
    }
    assert [tool.name for tool in tools] == ["jina_search_web"]
    assert tools[0].description == "Search the web [server: jina, original: search_web]"
    assert await tools[0].ainvoke({"query": "langgraph"}) == {"query": "langgraph"}
