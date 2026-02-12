"""Check MCP tools availability."""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.tools.mcp import initialize_mcp_tools
from app.progenitor.config import MCPConfig


async def check_mcp():
    """Check what MCP tools are available."""
    config = MCPConfig.from_env()
    print(f"MCP Config:")
    print(f"  Enabled: {config.enabled}")
    print(f"  Base URL: {config.base_url}")
    print(f"  Has API Key: {config.api_key is not None}")
    print()
    
    client, tools = await initialize_mcp_tools(config)
    print(f"Found {len(tools)} MCP tools:")
    for tool in tools:
        print(f"  - {tool.get('name')}: {tool.get('description', 'No description')}")
    
    # Check for primer specifically
    primer = next((t for t in tools if t.get('name') == 'primer'), None)
    if primer:
        print(f"\n✓ Found 'primer' tool!")
        print(f"  Description: {primer.get('description')}")
        print(f"  Input schema: {primer.get('inputSchema', {})}")
    else:
        print(f"\n⚠️ 'primer' tool not found")
    
    if client:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(check_mcp())
