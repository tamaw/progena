#!/usr/bin/env python
"""
Mock Progenitor for Backend Testing.

A simple echo server that simulates Progenitor's stdin/stdout interface.
Useful for testing the backend without the full agent running.
"""

import sys
import json


def mock_progenitor():
    """Run a simple echo server on stdin/stdout (synchronous for reliability)."""
    print("[Mock Progenitor] Started. Waiting for messages on stdin...", file=sys.stderr)
    sys.stderr.flush()

    while True:
        try:
            # Read from stdin (blocking) - synchronous is more reliable for subprocess IPC
            line = sys.stdin.readline()

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

                # Write response to stdout - flush immediately
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            except json.JSONDecodeError as e:
                error_response = {"content": f"JSON parse error: {e}"}
                sys.stdout.write(json.dumps(error_response) + "\n")
                sys.stdout.flush()

        except KeyboardInterrupt:
            print("[Mock Progenitor] Interrupted", file=sys.stderr)
            break
        except Exception as e:
            print(f"[Mock Progenitor] Error: {e}", file=sys.stderr)
            error_response = {"content": f"Server error: {e}"}
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    mock_progenitor()
