#!/usr/bin/env python
"""
Demo: Backend Usage Examples

Run with: python examples/demo.py [1|2|3]
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.backend.client import ProgenitorClient
from app.backend.config import ChatServerConfig


async def demo_async_client():
    """Demo: Direct async client usage."""
    print("\n" + "=" * 70)
    print("DEMO 1: Direct Async Client")
    print("=" * 70)

    config = ChatServerConfig(
        progenitor_script=Path(__file__).parent.parent / "tests" / "backend" / "mock_progenitor.py"
    )

    client = ProgenitorClient(config)
    await client.start()

    print(f"Progenitor running: {client.is_running}")

    messages = [
        "Hello, Progenitor!",
        "What can you do?",
        "Build me an agent to analyze customer sentiment",
    ]

    for msg in messages:
        print(f"\nUser: {msg}")
        response = await client.send_message(msg)
        print(f"Progenitor: {response}")

    await client.stop()
    print("\n✓ Demo complete")


def demo_http_api():
    """Demo: HTTP API usage."""
    print("\n" + "=" * 70)
    print("DEMO 2: HTTP API (REST)")
    print("=" * 70)
    print("\nServer must be running on http://localhost:8000")
    print("Start with: python cli.py serve\n")

    try:
        import requests

        base_url = "http://localhost:8000"

        # Health check
        print("1. Health Check")
        resp = requests.get(f"{base_url}/health", timeout=2)
        print(f"   Status: {resp.json()}")

        # List models
        print("\n2. List Models")
        resp = requests.get(f"{base_url}/v1/models")
        models = [m["id"] for m in resp.json()["data"]]
        print(f"   Available: {models}")

        print("\n✓ Demo complete")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("Make sure server is running: python cli.py serve")


def demo_openai_sdk():
    """Demo: Using official OpenAI SDK."""
    print("\n" + "=" * 70)
    print("DEMO 3: OpenAI SDK")
    print("=" * 70)
    print("\nServer must be running on http://localhost:8000")
    print("Start with: python cli.py serve\n")

    try:
        from openai import OpenAI

        client = OpenAI(
            base_url="http://localhost:8000/v1",
            api_key="not-used-locally",
        )

        response = client.chat.completions.create(
            model="progenitor-v1",
            messages=[
                {"role": "user", "content": "Hello!"},
            ],
            timeout=5,
        )

        print(f"Response: {response.choices[0].message.content}")
        print("\n✓ Demo complete")

    except ImportError:
        print("OpenAI SDK not installed.")
        print("Install with: pip install openai")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("Make sure server is running: python cli.py serve")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        demo = sys.argv[1]
        if demo == "1":
            asyncio.run(demo_async_client())
        elif demo == "2":
            demo_http_api()
        elif demo == "3":
            demo_openai_sdk()
        else:
            print(f"Unknown demo: {demo}")
            print("Usage: python examples/demo.py [1|2|3]")
    else:
        print("Backend Demos")
        print("=" * 70)
        print("\nRun one of these:")
        print("  python examples/demo.py 1  - Direct async client with mock")
        print("  python examples/demo.py 2  - HTTP API (requires server running)")
        print("  python examples/demo.py 3  - OpenAI SDK (requires server running)")
        print("\nOr start the server with:")
        print("  python cli.py serve")
