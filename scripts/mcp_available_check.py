"""Quick manual check for available MCP tools."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient


async def main() -> None:
    """Ask the agent which MCP tools are available and print the stream."""
    client = IPCClient()
    await client.connect()

    try:
        print("Asking: What MCP tools are available?\n")
        response_parts: list[str] = []

        async for event in client.send_message_stream_events(
            "What MCP tools do you have available? List them."
        ):
            if event.get("type") != "stream_chunk":
                continue
            response_parts.append(str(event.get("content", "")))

        print("Response:")
        print("".join(response_parts))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
