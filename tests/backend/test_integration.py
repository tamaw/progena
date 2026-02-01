#!/usr/bin/env python
"""
Integration test for Backend.

Tests the backend endpoints without needing Progenitor running.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi.testclient import TestClient
from app.backend.main import app


def test_endpoints():
    """Test the backend endpoints."""
    client = TestClient(app)

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

    # Test 3: Chat completions endpoint (no Progenitor)
    print("=" * 60)
    print("TEST 3: Chat Completions (no Progenitor - will fail)")
    print("=" * 60)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "progenitor-v1",
            "messages": [{"role": "user", "content": "Hello!"}],
        },
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:200]}")
    # Should fail because no Progenitor running
    assert response.status_code >= 400
    print("✓ PASSED (expected failure)\n")

    # Test 4: Simple chat endpoint (no Progenitor)
    print("=" * 60)
    print("TEST 4: Simple Chat (no Progenitor - will fail)")
    print("=" * 60)
    response = client.post(
        "/chat",
        json={"message": "Hello, Progenitor!"},
    )
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:200]}")
    # Should fail because no Progenitor running
    assert response.status_code >= 400
    print("✓ PASSED (expected failure)\n")

    print("=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)
    print("\nNext step: Implement Progenitor stdin/stdout interface for subprocess mode")


if __name__ == "__main__":
    test_endpoints()
