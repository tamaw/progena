"""Quick test to ask agent about MCP tools."""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient


async def ask_about_mcp():
    """Ask agent about MCP tools."""
    client = IPCClient()
    await client.connect()

    print("Asking: What MCP tools are available?\n")
    response_parts = []

    async for chunk in client.send_message_stream("What MCP tools do you have available? List them."):
        if isinstance(chunk, dict):
            response_parts.append(chunk.get("content", ""))
        else:
            response_parts.append(str(chunk))

    response = "".join(response_parts)
    print("Response:")
    print(response)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(ask_about_mcp())
