"""
Demo: OpenAI Unified Responses API

Shows the new /v1/responses endpoint (alias to /v1/chat/completions).
Both endpoints behave identically for backwards compatibility.
"""

import httpx
import sys


def demo_both_endpoints():
    """Test both /v1/chat/completions and /v1/responses endpoints."""
    base_url = "http://localhost:8000"

    request_data = {
        "model": "progenitor-v1",
        "messages": [
            {"role": "user", "content": "Hello, what's your status?"}
        ]
    }

    print("=" * 70)
    print("OpenAI API Compatibility Demo")
    print("=" * 70)

    # Test legacy endpoint
    print("\n1. Testing legacy /v1/chat/completions endpoint...")
    print("-" * 70)
    try:
        with httpx.Client() as client:
            response = client.post(
                f"{base_url}/v1/chat/completions",
                json=request_data,
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✓ Status: {response.status_code}")
                print(f"✓ Model: {result['model']}")
                print(f"✓ Response ID: {result['id']}")
                print(f"✓ Content: {result['choices'][0]['message']['content'][:100]}...")
            else:
                print(f"✗ Error {response.status_code}: {response.text}")
    except httpx.ConnectError:
        print("✗ Server not running. Start with: python cli.py serve")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Error: {e}")

    # Test new unified endpoint
    print("\n2. Testing new /v1/responses endpoint...")
    print("-" * 70)
    try:
        with httpx.Client() as client:
            response = client.post(
                f"{base_url}/v1/responses",
                json=request_data,
                timeout=10.0
            )

            if response.status_code == 200:
                result = response.json()
                print(f"✓ Status: {response.status_code}")
                print(f"✓ Model: {result['model']}")
                print(f"✓ Response ID: {result['id']}")
                print(f"✓ Content: {result['choices'][0]['message']['content'][:100]}...")
            else:
                print(f"✗ Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"✗ Error: {e}")

    print("\n" + "=" * 70)
    print("Both endpoints return identical responses!")
    print("Use /v1/responses for new integrations (recommended)")
    print("Use /v1/chat/completions for legacy compatibility")
    print("=" * 70)


if __name__ == "__main__":
    demo_both_endpoints()
