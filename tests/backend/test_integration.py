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
        # Test 1: Responses endpoint
        print("=" * 60)
        print("TEST 1: Responses API")
        print("=" * 60)
        request_id = "integration-response-request"
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "progenitor-v1",
                "input": "Hello!",
            },
            headers={"X-Request-ID": request_id},
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:200]}")
        assert response.status_code == 202
        assert response.headers["X-Request-ID"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        print("PASSED\n")

        # Test 2: List models (deleted)
        print("=" * 60)
        print("TEST 2: List Models (/v1/models) - deleted")
        print("=" * 60)
        response = client.get("/v1/models")
        print(f"Status: {response.status_code}")
        assert response.status_code == 404
        print("PASSED\n")

        # Test 3: Simple chat (deleted)
        print("=" * 60)
        print("TEST 3: Simple Chat - deleted")
        print("=" * 60)
        response = client.post(
            "/chat",
            json={"message": "Hello, Progenitor!"},
        )
        print(f"Status: {response.status_code}")
        assert response.status_code == 404
        print("PASSED\n")

        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    finally:
        backend_main.client = original_client


if __name__ == "__main__":
    test_endpoints()
