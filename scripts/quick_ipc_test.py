"""Quick manual IPC client communication check."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient


async def main() -> None:
    """Send a test message to Progenitor and print the response."""
    client = IPCClient(host="127.0.0.1", port=9500, timeout=90)

    try:
        print("Connecting to Progenitor...")
        connected = await client.connect()
        if not connected:
            print("Failed to connect")
            return

        print("Connected")
        response = await asyncio.wait_for(
            client.send_message("Hello, what is 1+1?"),
            timeout=90,
        )
        print(f"Response: {response[:200]}...")
    except TimeoutError:
        print("Timeout waiting for response")
    except Exception as exc:
        print(f"Error: {exc}")
    finally:
        await client.disconnect()
        print("Disconnected")


if __name__ == "__main__":
    asyncio.run(main())
