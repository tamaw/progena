"""Check MCP tools availability."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.config import MCPConfig
from app.progenitor.mcp import build_mcp_runtime_context


async def check_mcp():
    """Check what MCP tools are available."""
    config = MCPConfig()
    print("MCP Config:")
    print(f"  Enabled Servers: {[server.name for server in config.enabled_servers]}")
    print()

    runtime_servers, tools, _server_status = await build_mcp_runtime_context(config)
    print(f"Found {len(tools)} MCP tools:")
    for tool in tools:
        print(
            "  - "
            f"{tool.get('name')}: {tool.get('description', 'No description')} "
            f"(server={tool.get('mcp_server_name')})"
        )

    # Check for primer specifically
    primer = next((t for t in tools if t.get("name") == "primer"), None)
    if primer:
        print("\n✓ Found 'primer' tool!")
        print(f"  Description: {primer.get('description')}")
        print(f"  Input schema: {primer.get('inputSchema', {})}")
    else:
        print("\n⚠️ 'primer' tool not found")

    for server in runtime_servers.values():
        client = server.get("client")
        if client is not None:
            await client.aclose()


if __name__ == "__main__":
    asyncio.run(check_mcp())
