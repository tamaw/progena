#!/usr/bin/env python
"""Integration test for backend endpoints with a mocked Progenitor client."""


def test_endpoints(client):
    """Test the backend endpoints."""
    import app.backend.main as backend_main

    original_client = backend_main.client

    class _MockClient:
        async def send_message(
            self,
            message: str,
            request_id: str | None = None,
            metadata: dict[str, object] | None = None,
            include_reasoning: bool | None = None,
        ):
            del message, request_id, metadata
            del include_reasoning
            from app.backend.models import AgentResponse

            return AgentResponse(response="mocked response", reasoning=None, reasoning_tokens=None)

    backend_main.client = _MockClient()

    try:
        # Test 1: Health check
        print("=" * 60)
        print("TEST 1: Health Check")
        print("=" * 60)
        response = client.get("/health")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        assert response.status_code == 200
        print("✓ PASSED\n")

        # Test 2: List models
        print("=" * 60)
        print("TEST 2: List Models (/v1/models)")
        print("=" * 60)
        response = client.get("/v1/models")
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Response: {data}")
        assert response.status_code == 200
        assert data["object"] == "list"
        assert len(data["data"]) > 0
        print("✓ PASSED\n")

        # Test 3: Responses endpoint
        print("=" * 60)
        print("TEST 3: Responses API")
        print("=" * 60)
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Hello!",
            },
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:200]}")
        assert response.status_code == 200
        assert response.json()["output"][0]["content"][0]["text"] == "mocked response"
        print("✓ PASSED\n")

        # Test 4: Simple chat endpoint
        print("=" * 60)
        print("TEST 4: Simple Chat")
        print("=" * 60)
        response = client.post(
            "/chat",
            json={"message": "Hello, Progenitor!"},
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:200]}")
        assert response.status_code == 200
        assert response.json() == {"response": "mocked response"}
        print("✓ PASSED\n")

        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    finally:
        backend_main.client = original_client


if __name__ == "__main__":
    test_endpoints()
