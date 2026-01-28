#!/usr/bin/env python
"""
New Agent - A2A Agent Template

A basic agent-to-agent (A2A) implementation that receives messages
via stdin and sends responses via stdout using JSON serialization.

Usage:
    python agent.py < input.json > output.json
"""

import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime


@dataclass
class Message:
    """Standard message format for A2A communication."""
    content: str
    role: str = "assistant"
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class Request:
    """Standard request format."""
    id: str
    message: str
    metadata: Optional[dict] = None


@dataclass
class Response:
    """Standard response format."""
    id: str
    message: str
    status: str = "success"
    error: Optional[str] = None


def process_message(request: Request) -> Response:
    """
    Process incoming message and generate response.

    Args:
        request: The incoming request

    Returns:
        Response object
    """
    try:
        # Echo the message as a simple implementation
        response_text = f"New Agent received: {request.message}"

        return Response(
            id=request.id,
            message=response_text,
            status="success",
        )
    except Exception as e:
        return Response(
            id=request.id,
            message="",
            status="error",
            error=str(e),
        )


def main():
    """Main agent loop - read from stdin, write to stdout."""
    try:
        # Read JSON request from stdin
        request_data = json.loads(sys.stdin.read())
        request = Request(**request_data)

        # Process the message
        response = process_message(request)

        # Write JSON response to stdout
        print(json.dumps(asdict(response)))

    except json.JSONDecodeError as e:
        error_response = Response(
            id="unknown",
            message="",
            status="error",
            error=f"Invalid JSON: {str(e)}",
        )
        print(json.dumps(asdict(error_response)))
    except Exception as e:
        error_response = Response(
            id="unknown",
            message="",
            status="error",
            error=f"Agent error: {str(e)}",
        )
        print(json.dumps(asdict(error_response)))


if __name__ == "__main__":
    main()
