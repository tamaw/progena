#!/usr/bin/env python
"""Progenitor Chat CLI - Alternative using httpx for async streaming.

This version uses httpx for better async/streaming support.
Install: pip install httpx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration defaults
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_MODEL = "progenitor-v1"
DEFAULT_TIMEOUT = 300


class ChatClient:
    """HTTP client for the Progenitor chat server."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def health_check(self) -> dict[str, Any]:
        """Check server health."""
        resp = self._client.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()

    def send_message(self, text: str) -> str:
        """Send a message and return the response (non-streaming)."""
        payload = {
            "model": self.model,
            "input": text,
            "stream": False,
        }
        resp = self._client.post(
            f"{self.base_url}/v1/responses",
            json=payload,
        )
        resp.raise_for_status()
        return self._extract_text(resp.json())

    def send_message_stream(self, text: str) -> str:
        """Send a message and stream the response."""
        payload = {
            "model": self.model,
            "input": text,
            "stream": True,
        }

        accumulated: list[str] = []
        printed_header = False

        with self._client.stream(
            "POST",
            f"{self.base_url}/v1/responses",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()

            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("object", "")

                # Responses API streaming format
                if event_type == "response.output_text.delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        if not printed_header:
                            sys.stdout.write("\n\033[1;32mProgenitor:\033[0m ")
                            printed_header = True
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        accumulated.append(text)

                # Chat Completions streaming format
                elif event_type == "chat.completion.chunk":
                    for choice in event.get("choices", []):
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            if not printed_header:
                                sys.stdout.write("\n\033[1;32mProgenitor:\033[0m ")
                                printed_header = True
                            sys.stdout.write(content)
                            sys.stdout.flush()
                            accumulated.append(content)

        if printed_header:
            print()

        return "".join(accumulated)

    def _extract_text(self, response: dict[str, Any]) -> str:
        """Extract text from response payload."""
        # Responses API format
        output = response.get("output", [])
        for item in output:
            if isinstance(item, dict) and item.get("role") == "assistant":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        return content.get("text", "")

        # Chat completions fallback
        for choice in response.get("choices", []):
            msg = choice.get("message", {})
            if msg.get("content"):
                return msg["content"]

        return ""


def print_banner(url: str, model: str) -> None:
    """Print the CLI banner."""
    print()
    print("\033[1;36m╔" + "═" * 58 + "╗\033[0m")
    print("\033[1;36m║" + " " * 18 + "Progenitor Chat CLI" + " " * 21 + "║\033[0m")
    print("\033[1;36m╚" + "═" * 58 + "╝\033[0m")
    print()
    print(f"  📡 Server: {url}")
    print(f"  🤖 Model:  {model}")
    print()
    print("  Commands: /exit  /stream on|off  /help")
    print()
    print("\033[1;36m" + "─" * 60 + "\033[0m")
    print()


def print_help() -> None:
    """Print help message."""
    print("""
\033[1;33mCommands:\033[0m
  /exit, /quit, /bye    Exit the chat
  /stream on|off        Toggle streaming (default: on)
  /health               Check server health
  /help                 Show this help

\033[1;33mEnvironment Variables:\033[0m
  PROGENITOR_CHAT_URL   Server URL (default: http://127.0.0.1:8000)
  PROGENITOR_MODEL      Model name (default: progenitor-v1)
  PROGENITOR_TIMEOUT    Request timeout in seconds (default: 300)
""")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Interactive chat CLI for Progenitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=os.getenv("PROGENITOR_CHAT_URL", DEFAULT_URL),
        help=f"Chat server URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("PROGENITOR_MODEL", DEFAULT_MODEL),
        help=f"Model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("PROGENITOR_TIMEOUT", str(DEFAULT_TIMEOUT))),
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming by default",
    )
    parser.add_argument(
        "-m", "--message",
        help="Send a single message and exit",
    )

    args = parser.parse_args()

    client = ChatClient(
        base_url=args.url,
        model=args.model,
        timeout=args.timeout,
    )

    # Single message mode
    if args.message:
        try:
            if args.no_stream:
                response = client.send_message(args.message)
                print(response)
            else:
                client.send_message_stream(args.message)
        except httpx.HTTPError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            client.close()
        return

    # Interactive mode
    print_banner(args.url, args.model)
    streaming = not args.no_stream

    try:
        while True:
            try:
                user_input = input("\033[1;34mYou:\033[0m ").strip()

                if not user_input:
                    continue

                cmd = user_input.lower()

                if cmd in ("/exit", "/quit", "/bye"):
                    print("\n\033[1;33mGoodbye! 👋\033[0m\n")
                    break

                if cmd == "/help":
                    print_help()
                    continue

                if cmd == "/health":
                    try:
                        health = client.health_check()
                        status = "✅" if health.get("status") == "ok" else "❌"
                        progenitor = "✅" if health.get("progenitor_running") else "⏸️"
                        print(f"\n  Server: {status}  Progenitor: {progenitor}\n")
                    except Exception as e:
                        print(f"\n  ❌ Health check failed: {e}\n")
                    continue

                if cmd.startswith("/stream"):
                    parts = cmd.split()
                    if len(parts) == 2:
                        if parts[1] == "on":
                            streaming = True
                            print("\033[1;33m(streaming enabled)\033[0m")
                        elif parts[1] == "off":
                            streaming = False
                            print("\033[1;33m(streaming disabled)\033[0m")
                        else:
                            print("\033[1;31mUsage: /stream on|off\033[0m")
                    else:
                        state = "on" if streaming else "off"
                        print(f"\033[1;33m(streaming is {state})\033[0m")
                    continue

                if cmd.startswith("/"):
                    print(f"\033[1;31mUnknown command: {user_input}\033[0m")
                    continue

                # Send message
                try:
                    if streaming:
                        response = client.send_message_stream(user_input)
                        if not response:
                            # Fallback to non-streaming
                            response = client.send_message(user_input)
                            print(f"\n\033[1;32mProgenitor:\033[0m {response}")
                    else:
                        response = client.send_message(user_input)
                        print(f"\n\033[1;32mProgenitor:\033[0m {response}")
                except httpx.HTTPError as e:
                    print(f"\n\033[1;31mError: {e}\033[0m")
                    print(f"Make sure the server is running at {args.url}")

            except KeyboardInterrupt:
                print("\n\n\033[1;33mGoodbye! 👋\033[0m\n")
                break
            except EOFError:
                print("\n\n\033[1;33mGoodbye! 👋\033[0m\n")
                break

    finally:
        client.close()


if __name__ == "__main__":
    main()
