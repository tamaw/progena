import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path when running the script directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.progenitor.ipc.client import create_ipc_client


async def main():
    client = create_ipc_client()
    connected = await client.connect()
    print("connected:", connected)
    try:
        resp = await client.send_message("hi")
        print("response:", resp)
    except Exception as e:
        print("error:", repr(e))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
