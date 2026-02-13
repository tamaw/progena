"""Quick test of IPC client communication."""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient


async def test_message():
    """Send a test message to Progenitor."""
    client = IPCClient(host="127.0.0.1", port=9500, timeout=90)

    try:
        print("Connecting to Progenitor...")
        connected = await client.connect()
        if not connected:
            print("❌ Failed to connect")
            return

        print("✓ Connected")

        print("Sending message...")
        response = await asyncio.wait_for(
            client.send_message("Hello, what is 1+1?"),
            timeout=90
        )

        print(f"✓ Got response: {response[:200]}...")

    except TimeoutError:
        print("❌ Timeout waiting for response")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await client.disconnect()
        print("Disconnected")


if __name__ == "__main__":
    asyncio.run(test_message())
