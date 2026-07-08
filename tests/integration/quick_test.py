"""Quick test of Progenitor runtime HTTP + SSE chat communication."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx

from tests.integration.helpers import (
    DEFAULT_BASE_URL,
    is_progenitor_running,
    send_message_and_collect,
)


async def test_message():
    """Send a test message to Progenitor."""
    print("Connecting to Progenitor...")
    if not await is_progenitor_running():
        print("❌ Failed to connect")
        return
    print("✓ Connected")

    async with httpx.AsyncClient(
        base_url=DEFAULT_BASE_URL, timeout=httpx.Timeout(10.0, read=None)
    ) as client:
        print("Sending message...")
        try:
            result = await asyncio.wait_for(
                send_message_and_collect(client, "Hello, what is 1+1?", "quick_test"),
                timeout=90,
            )
        except TimeoutError:
            print("❌ Timeout waiting for response")
            return

        if result["success"]:
            print(f"✓ Got response: {result['response'][:200]}...")
        else:
            print(f"❌ Error: {result['error']}")


if __name__ == "__main__":
    asyncio.run(test_message())
