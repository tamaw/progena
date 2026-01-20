#!/usr/bin/env python
"""
Mock Progenitor for Chat Server Testing.

A simple echo server that simulates Progenitor's stdin/stdout interface.
Useful for testing the chat server without the full agent running.
"""

import sys
import json
import asyncio


async def mock_progenitor():
    """Run a simple echo server on stdin/stdout."""
    print("[Mock Progenitor] Started. Waiting for messages on stdin...", file=sys.stderr)
    sys.stderr.flush()

    loop = asyncio.get_event_loop()

    while True:
        try:
            # Read from stdin (blocking)
            line = await loop.run_in_executor(None, sys.stdin.readline)

            if not line:
                break

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                message_type = data.get("type")
                content = data.get("content", "")

                # Echo back with a simple response
                if message_type == "user_message":
                    response = {
                        "content": f"Echo: {content}\n\n(This is the mock Progenitor responding)"
                    }
                else:
                    response = {"content": f"Unknown message type: {message_type}"}

                # Write response to stdout
                print(json.dumps(response), flush=True)

            except json.JSONDecodeError as e:
                error_response = {"content": f"JSON parse error: {e}"}
                print(json.dumps(error_response), flush=True)

        except KeyboardInterrupt:
            print("[Mock Progenitor] Interrupted", file=sys.stderr)
            break
        except Exception as e:
            print(f"[Mock Progenitor] Error: {e}", file=sys.stderr)
            error_response = {"content": f"Server error: {e}"}
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    asyncio.run(mock_progenitor())
