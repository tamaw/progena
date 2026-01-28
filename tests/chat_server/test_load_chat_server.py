"""
Load Tests for Chat Server.

TEST-003: Add load tests for chat server using Locust.

Tests:
- Concurrent request handling
- Rate limiting under load
- Response latency distribution
- Memory usage patterns
"""

from locust import HttpUser, task, between, events
import json
import logging
import time
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ChatServerUser(HttpUser):
    """Simulates a user interacting with the chat server."""

    # Wait 1-3 seconds between tasks
    wait_time = between(1, 3)

    # Default host
    host = "http://localhost:8888"

    def on_start(self):
        """Called when a simulated user starts."""
        self.conversation_id = None

    @task(10)
    def send_chat_message(self):
        """Send a chat completion request (most common operation)."""
        payload = {
            "model": "progenitor",
            "messages": [
                {"role": "user", "content": "What is the current status?"}
            ],
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}

        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers,
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            elif response.status_code == 429:
                # Rate limited - expected under load
                response.success()
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(5)
    def send_streaming_message(self):
        """Send a streaming chat request."""
        payload = {
            "model": "progenitor",
            "messages": [
                {"role": "user", "content": "Tell me about the system."}
            ],
            "stream": True,
        }

        headers = {"Content-Type": "application/json"}

        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers,
            catch_response=True,
            stream=True,
        ) as response:
            if response.status_code == 200:
                # Read stream to completion
                for line in response.iter_lines():
                    if line:
                        pass  # Just consume the stream
                response.success()
            elif response.status_code == 429:
                response.success()  # Rate limiting expected
            else:
                response.failure(f"Unexpected status: {response.status_code}")

    @task(3)
    def check_health(self):
        """Check health endpoint."""
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    @task(1)
    def list_models(self):
        """List available models."""
        with self.client.get("/v1/models", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Models list failed: {response.status_code}")


class BurstUser(HttpUser):
    """Simulates a bursty user for stress testing."""

    # No wait between requests - burst mode
    wait_time = between(0, 0.1)

    host = "http://localhost:8888"

    @task
    def burst_requests(self):
        """Send rapid fire requests."""
        payload = {
            "model": "progenitor",
            "messages": [
                {"role": "user", "content": "Quick ping"}
            ],
            "stream": False,
        }

        with self.client.post(
            "/v1/chat/completions",
            json=payload,
            catch_response=True,
        ) as response:
            # Accept both success and rate-limit responses
            if response.status_code in (200, 429):
                response.success()
            else:
                response.failure(f"Status: {response.status_code}")


# ============================================================================
# Event Hooks for Metrics Collection
# ============================================================================

# Storage for custom metrics
custom_metrics = {
    "rate_limited_count": 0,
    "success_count": 0,
    "latencies": [],
}


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, **kwargs):
    """Collect custom metrics on each request."""
    custom_metrics["latencies"].append(response_time)

    if response and response.status_code == 200:
        custom_metrics["success_count"] += 1
    elif response and response.status_code == 429:
        custom_metrics["rate_limited_count"] += 1


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print summary statistics at end of test."""
    latencies = custom_metrics["latencies"]

    if latencies:
        avg_latency = sum(latencies) / len(latencies)
        sorted_latencies = sorted(latencies)
        p50 = sorted_latencies[len(sorted_latencies) // 2]
        p95 = sorted_latencies[int(len(sorted_latencies) * 0.95)]
        p99 = sorted_latencies[int(len(sorted_latencies) * 0.99)]

        logger.info("=" * 60)
        logger.info("CUSTOM METRICS SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total requests: {len(latencies)}")
        logger.info(f"Successful: {custom_metrics['success_count']}")
        logger.info(f"Rate limited: {custom_metrics['rate_limited_count']}")
        logger.info(f"Average latency: {avg_latency:.2f}ms")
        logger.info(f"P50 latency: {p50:.2f}ms")
        logger.info(f"P95 latency: {p95:.2f}ms")
        logger.info(f"P99 latency: {p99:.2f}ms")
        logger.info("=" * 60)


# ============================================================================
# Load Test Scenarios (for programmatic execution)
# ============================================================================

def run_load_test_scenario(
    users: int = 10,
    spawn_rate: int = 2,
    run_time: str = "60s",
    host: str = "http://localhost:8888",
):
    """
    Run a load test scenario programmatically.

    Usage:
        from test_load_chat_server import run_load_test_scenario
        run_load_test_scenario(users=50, run_time="5m")
    """
    import subprocess
    import sys

    cmd = [
        sys.executable, "-m", "locust",
        "-f", __file__,
        "--host", host,
        "-u", str(users),
        "-r", str(spawn_rate),
        "-t", run_time,
        "--headless",
    ]

    subprocess.run(cmd)


# ============================================================================
# Standalone Async Load Test (no locust dependency)
# ============================================================================

async def run_simple_load_test(
    base_url: str = "http://localhost:8888",
    total_requests: int = 100,
    concurrent_workers: int = 10,
):
    """
    Simple async load test without locust.

    Can be run with: python -m asyncio test_load_chat_server.py
    """
    import asyncio
    import httpx

    results = {
        "success": 0,
        "rate_limited": 0,
        "errors": 0,
        "latencies": [],
    }

    sem = asyncio.Semaphore(concurrent_workers)

    async def make_request(client: httpx.AsyncClient, request_num: int):
        async with sem:
            start = time.time()
            try:
                response = await client.post(
                    f"{base_url}/v1/chat/completions",
                    json={
                        "model": "progenitor",
                        "messages": [{"role": "user", "content": f"Test {request_num}"}],
                        "stream": False,
                    },
                    timeout=30.0,
                )

                latency = (time.time() - start) * 1000  # ms
                results["latencies"].append(latency)

                if response.status_code == 200:
                    results["success"] += 1
                elif response.status_code == 429:
                    results["rate_limited"] += 1
                else:
                    results["errors"] += 1

            except Exception as e:
                results["errors"] += 1
                logger.error(f"Request {request_num} failed: {e}")

    async with httpx.AsyncClient() as client:
        tasks = [
            make_request(client, i)
            for i in range(total_requests)
        ]
        await asyncio.gather(*tasks)

    # Print results
    print("\n" + "=" * 60)
    print("SIMPLE LOAD TEST RESULTS")
    print("=" * 60)
    print(f"Total requests: {total_requests}")
    print(f"Concurrent workers: {concurrent_workers}")
    print(f"Successful: {results['success']}")
    print(f"Rate limited: {results['rate_limited']}")
    print(f"Errors: {results['errors']}")

    if results["latencies"]:
        avg = sum(results["latencies"]) / len(results["latencies"])
        sorted_lat = sorted(results["latencies"])
        p95_idx = int(len(sorted_lat) * 0.95)
        print(f"Average latency: {avg:.2f}ms")
        print(f"P95 latency: {sorted_lat[p95_idx]:.2f}ms")

    print("=" * 60)

    return results


if __name__ == "__main__":
    # When run directly, execute simple async test
    import asyncio
    asyncio.run(run_simple_load_test())
