#!/usr/bin/env python
"""Progenitor Chat CLI - Interactive chat client for the backend.

Connects to the backend and provides a terminal-based chat interface
with streaming support and conversation history.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib import error, request

# Configuration from environment
BASE_URL = os.getenv("PROGENITOR_CHAT_URL", "http://127.0.0.1:8000/v1/responses")
MODEL = os.getenv("PROGENITOR_MODEL", "progenitor-v1")
REQUEST_TIMEOUT = int(os.getenv("PROGENITOR_TIMEOUT", "300"))
LOG_RESPONSES = os.getenv("PROGENITOR_LOG_RESPONSES", "false").lower() in ("true", "1", "yes")


def _build_payload(text: str, *, stream: bool = False) -> dict[str, Any]:
    """Build the request payload for the Responses API."""
    payload: dict[str, Any] = {
        "model": MODEL,
        "input": text,
    }
    if stream:
        payload["stream"] = True
    return payload


def _extract_text(response: dict[str, Any]) -> str:
    """Extract response text from various response formats."""
    # Responses API format
    output = response.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            # Check for message content
            if item.get("role") == "assistant":
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") in ("output_text", "text"):
                        text = content.get("text") or content.get("content")
                        if isinstance(text, str):
                            texts.append(text)
            # Direct text type
            elif item.get("type") == "message":
                for content in item.get("content", []):
                    if isinstance(content, dict):
                        text = content.get("text") or content.get("content")
                        if isinstance(text, str):
                            texts.append(text)
        if texts:
            return "".join(texts)

    # Chat Completions format fallback
    for choice in response.get("choices", []):
        message = choice.get("message")
        if isinstance(message, dict):
            text = message.get("content")
            if isinstance(text, str):
                return text

    # Simple chat format
    if "response" in response:
        return str(response["response"])

    return ""


class Spinner:
    """A simple terminal spinner for feedback during long operations."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._got_text = threading.Event()

    def start(self) -> None:
        """Start the spinner animation."""
        self._stop_event.clear()
        self._got_text.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        idx = 0
        while not self._stop_event.is_set():
            if self._got_text.is_set():
                break
            frame = frames[idx % len(frames)]
            sys.stdout.write(f"\r{frame} Thinking...")
            sys.stdout.flush()
            time.sleep(0.1)
            idx += 1
        if not self._got_text.is_set():
            sys.stdout.write("\r" + " " * 15 + "\r")
            sys.stdout.flush()

    def text_received(self) -> None:
        """Signal that text has been received (stops spinner cleanly)."""
        self._got_text.set()

    def stop(self) -> None:
        """Stop the spinner animation."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1)


def _log_response(data: dict[str, Any], log_file: Path) -> None:
    """Log response to JSONL file if logging is enabled."""
    if LOG_RESPONSES:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")


def _post_request(payload: dict[str, Any]) -> str:
    """Make a non-streaming POST request and return the response text."""
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        BASE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    spinner = Spinner()
    spinner.start()

    log_file = Path(__file__).parent / "logs" / "chat_cli.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            response_data = json.load(resp)
    finally:
        spinner.stop()

    _log_response(response_data, log_file)
    return _extract_text(response_data)


def _post_streaming(payload: dict[str, Any]) -> str:
    """Stream a POST request and print text deltas as they arrive."""
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        BASE_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    spinner = Spinner()
    spinner.start()

    log_file = Path(__file__).parent / "logs" / "chat_cli.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    accumulated_text: list[str] = []
    printed_prefix = False

    try:
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")

                # Skip empty lines and event type markers
                if not line or line.startswith("event:"):
                    continue

                # Parse SSE data lines
                if not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                _log_response(event, log_file)

                event_type = event.get("object", "")

                # Handle streaming text deltas (Responses API format)
                if event_type == "response.output_text.delta":
                    delta = event.get("delta", {})
                    text = delta.get("text", "")
                    if text:
                        spinner.text_received()
                        spinner.stop()
                        if not printed_prefix:
                            sys.stdout.write("\r" + " " * 15 + "\r")  # Clear spinner
                            sys.stdout.write("\n\033[1;32mProgenitor:\033[0m ")
                            printed_prefix = True
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        accumulated_text.append(text)

                # Handle Chat Completions streaming format
                elif event_type == "chat.completion.chunk":
                    for choice in event.get("choices", []):
                        delta = choice.get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            spinner.text_received()
                            spinner.stop()
                            if not printed_prefix:
                                sys.stdout.write("\r" + " " * 15 + "\r")
                                sys.stdout.write("\n\033[1;32mProgenitor:\033[0m ")
                                printed_prefix = True
                            sys.stdout.write(content)
                            sys.stdout.flush()
                            accumulated_text.append(content)

    finally:
        spinner.stop()
        if printed_prefix:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return "".join(accumulated_text)


def send_message(text: str, *, stream: bool = True) -> str:
    """Send a message and return the response.

    Tries streaming first, falls back to non-streaming if no text received.
    """
    if stream:
        payload = _build_payload(text, stream=True)
        response_text = _post_streaming(payload)

        if response_text:
            return response_text

        # Fallback to non-streaming
        print("(streaming returned no text, retrying without streaming...)")

    payload = _build_payload(text, stream=False)
    response_text = _post_request(payload)

    if response_text:
        print(f"\n\033[1;32mProgenitor:\033[0m {response_text}")
    else:
        print("\n\033[1;33m(no response text)\033[0m")

    return response_text


def print_banner() -> None:
    """Print the CLI banner."""
    print()
    print("\033[1;36m" + "=" * 60 + "\033[0m")
    print("\033[1;36m  Progenitor Chat CLI\033[0m")
    print("\033[1;36m" + "=" * 60 + "\033[0m")
    print()
    print(f"  Server: {BASE_URL}")
    print(f"  Model:  {MODEL}")
    print()
    print("  Commands:")
    print("    /exit, /quit, /bye  - Exit the chat")
    print("    /stream on|off      - Toggle streaming mode")
    print("    /help               - Show this help")
    print()
    print("\033[1;36m" + "-" * 60 + "\033[0m")
    print()


def print_help() -> None:
    """Print help information."""
    print()
    print("\033[1;33mAvailable Commands:\033[0m")
    print("  /exit, /quit, /bye  - Exit the chat")
    print("  /stream on|off      - Toggle streaming mode (default: on)")
    print("  /help               - Show this help")
    print()
    print("\033[1;33mEnvironment Variables:\033[0m")
    print("  PROGENITOR_CHAT_URL    - Backend URL")
    print("  PROGENITOR_MODEL       - Model name")
    print("  PROGENITOR_TIMEOUT     - Request timeout (seconds)")
    print("  PROGENITOR_LOG_RESPONSES - Log responses to file (true/false)")
    print()


def main() -> None:
    """Main entry point for the chat CLI."""
    print_banner()

    streaming = True

    while True:
        try:
            user_input = input("\033[1;34mYou:\033[0m ").strip()

            if not user_input:
                continue

            # Handle commands
            normalized = user_input.lower()

            if normalized in ("/exit", "/quit", "/bye"):
                print("\n\033[1;33mGoodbye!\033[0m\n")
                break

            if normalized == "/help":
                print_help()
                continue

            if normalized.startswith("/stream"):
                parts = normalized.split()
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
                    print(f"\033[1;33m(streaming is {'on' if streaming else 'off'})\033[0m")
                continue

            if normalized.startswith("/"):
                print(f"\033[1;31mUnknown command: {user_input}\033[0m")
                print("Type /help for available commands.")
                continue

            # Send message to server
            try:
                send_message(user_input, stream=streaming)
            except error.URLError as exc:
                print(f"\n\033[1;31mError: Failed to reach backend: {exc}\033[0m")
                print(f"Make sure the server is running at {BASE_URL}")
                continue

        except KeyboardInterrupt:
            print("\n\n\033[1;33mGoodbye!\033[0m\n")
            break
        except EOFError:
            print("\n\n\033[1;33mGoodbye!\033[0m\n")
            break
        except Exception as exc:
            print(f"\n\033[1;31mError: {exc}\033[0m")


if __name__ == "__main__":
    main()
