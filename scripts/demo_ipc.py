#!/usr/bin/env python
"""
Demo script to test IPC communication between client and server.

Usage:
    # Start the Progenitor agent with IPC enabled (in one terminal):
    python -m app.progenitor.main

    # Then run this script (in another terminal):
    python scripts/demo_ipc.py
"""

import asyncio
import argparse
import logging
import sys

# Add parent directory to path
sys.path.insert(0, str(__file__).rsplit("scripts", 1)[0])

from app.progenitor.ipc import IPCClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def demo_send_message(client: IPCClient, message: str):
    """Demo: Send a single message and get response."""
    logger.info("Sending message: %s", message)
    response = await client.send_message(message)
    logger.info("Response: %s", response)
    return response


async def demo_stream_message(client: IPCClient, message: str):
    """Demo: Send a message and stream the response."""
    logger.info("Streaming message: %s", message)
    full_response = ""
    async for chunk in client.send_message_stream(message):
        print(chunk, end="", flush=True)
        full_response += chunk
    print()  # Newline after streaming
    logger.info("Full response length: %d chars", len(full_response))
    return full_response


async def main():
    parser = argparse.ArgumentParser(description="Demo IPC communication")
    parser.add_argument("--host", default="127.0.0.1", help="IPC server host")
    parser.add_argument("--port", type=int, default=9500, help="IPC server port")
    parser.add_argument("--message", default="Hello, Progenitor!", help="Message to send")
    parser.add_argument("--stream", action="store_true", help="Use streaming mode")
    parser.add_argument("--interactive", action="store_true", help="Interactive mode")
    args = parser.parse_args()

    client = IPCClient(host=args.host, port=args.port, timeout=300)

    try:
        logger.info("Connecting to Progenitor at %s:%d...", args.host, args.port)
        connected = await client.connect()
        if not connected:
            logger.error("Failed to connect. Is Progenitor running with IPC enabled?")
            logger.error("Start it with: python -m app.progenitor.main")
            return 1

        logger.info("Connected! Client status: %s", client.get_status())

        if args.interactive:
            # Interactive mode
            print("\nInteractive mode. Type 'quit' to exit, 'stream <msg>' for streaming.")
            while True:
                try:
                    user_input = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break

                if not user_input:
                    continue
                if user_input.lower() == "quit":
                    break

                if user_input.lower().startswith("stream "):
                    await demo_stream_message(client, user_input[7:])
                else:
                    await demo_send_message(client, user_input)
        else:
            # Single message mode
            if args.stream:
                await demo_stream_message(client, args.message)
            else:
                await demo_send_message(client, args.message)

        return 0

    except ConnectionError as e:
        logger.error("Connection error: %s", e)
        return 1
    except Exception as e:
        logger.exception("Error: %s", e)
        return 1
    finally:
        await client.disconnect()
        logger.info("Disconnected")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
