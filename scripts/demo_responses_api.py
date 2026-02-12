"""
Demo: Progenitor Responses API

Shows the /v1/responses endpoint for interacting with the Progenitor agent.
"""

import sys

import httpx


def demo_responses_api():
    """Test the /v1/responses endpoint."""
    base_url = "http://localhost:8000"

    request_data = {
        "model": "progenitor-v1",
        "input": "Hello, what's your status?"
    }

    print("=" * 70)
    print("Progenitor Responses API Demo")
    print("=" * 70)

    print("\nTesting /v1/responses endpoint...")
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
                print(f"Status: {response.status_code}")
                print(f"Model: {result['model']}")
                print(f"Response ID: {result['id']}")
                output_text = result['output'][0]['content'][0]['text']
                print(f"Content: {output_text[:100]}...")
            else:
                print(f"Error {response.status_code}: {response.text}")
    except httpx.ConnectError:
        print("Server not running. Start with: python cli.py serve")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")

    print("\n" + "=" * 70)
    print("Use /v1/responses for all API interactions")
    print("=" * 70)


if __name__ == "__main__":
    demo_responses_api()
