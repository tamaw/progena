"""Quick test to ask agent about MCP tools."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from tests.integration.helpers import DEFAULT_BASE_URL, send_message_and_collect


async def ask_about_mcp():
    """Ask agent about MCP tools."""
    async with httpx.AsyncClient(
        base_url=DEFAULT_BASE_URL, timeout=httpx.Timeout(10.0, read=None)
    ) as client:
        print("Asking: What MCP tools are available?\n")

        result = await send_message_and_collect(
            client, "What MCP tools do you have available? List them.", "mcp_available"
        )

        print("Response:")
        print(result["response"] if result["success"] else f"Error: {result['error']}")


if __name__ == "__main__":
    asyncio.run(ask_about_mcp())
