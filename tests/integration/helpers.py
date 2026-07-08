"""
Helper utilities for integration tests.

Provides functions to check if Progenitor's runtime HTTP server is running,
wait for it to be ready, and stream chat requests over HTTP + SSE.
"""

import asyncio
import json
import logging
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:9501"
STARTUP_TIMEOUT = 30.0


async def is_progenitor_running(base_url: str = DEFAULT_BASE_URL) -> bool:
    """Check if the Progenitor runtime HTTP server is running and healthy.

    Args:
        base_url: Runtime HTTP base URL.

    Returns:
        True if the server is running and responsive, False otherwise.
    """
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
            response = await client.get("/health")
            return response.status_code == 200
    except Exception as e:
        logger.debug(f"Health check failed: {e}")
        return False


async def wait_for_progenitor(
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = STARTUP_TIMEOUT,
    retry_interval: float = 2.0,
) -> bool:
    """Wait for the Progenitor runtime HTTP server to become available.

    Args:
        base_url: Runtime HTTP base URL.
        timeout: Maximum time to wait in seconds.
        retry_interval: Time between connection attempts.

    Returns:
        True if the server became available, False if timeout.
    """
    logger.info(f"Waiting for Progenitor at {base_url}...")

    start_time = asyncio.get_event_loop().time()
    attempts = 0

    while True:
        attempts += 1
        elapsed = asyncio.get_event_loop().time() - start_time

        if elapsed > timeout:
            logger.error(f"Timeout waiting for Progenitor after {attempts} attempts")
            return False

        if await is_progenitor_running(base_url):
            logger.info(f"Progenitor is ready (attempt {attempts}, {elapsed:.1f}s)")
            return True

        logger.debug(f"Attempt {attempts}: Not ready yet, retrying in {retry_interval}s...")
        await asyncio.sleep(retry_interval)


async def iter_chat_sse_frames(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse ``event:``/``data:`` SSE frames from a streaming httpx response."""
    event_name: str | None = None
    async for line in response.aiter_lines():
        if line == "" or line.startswith(":"):
            continue
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
            continue
        if line.startswith("data: "):
            if event_name is None:
                continue
            yield event_name, json.loads(line[len("data: ") :])
            event_name = None


async def send_message_and_collect(
    client: httpx.AsyncClient,
    message: str,
    test_name: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Send a message to Progenitor over HTTP and collect the streamed response.

    Args:
        client: httpx client pointed at the runtime's base URL.
        message: User message to send.
        test_name: Name of the test (for logging).
        timeout: Timeout in seconds.

    Returns:
        Dictionary with test results.
    """
    logger.info(f"[{test_name}] Sending: {message[:100]}...")

    result: dict[str, Any] = {
        "test_name": test_name,
        "timestamp": datetime.now().isoformat(),
        "request": message,
        "response": "",
        "chunks": [],
        "error": None,
        "duration_seconds": 0,
        "success": False,
    }

    start_time = asyncio.get_event_loop().time()

    try:
        async with client.stream(
            "POST", "/v1/chat", json={"message": message}, timeout=timeout
        ) as response:
            async for event_name, data in iter_chat_sse_frames(response):
                if event_name == "error":
                    raise RuntimeError(str(data.get("error", "unknown error")))
                if event_name == "done":
                    break
                if event_name != "stream_chunk":
                    continue
                chunk = str(data.get("content", ""))
                if chunk:
                    result["chunks"].append(chunk)
                    result["response"] += chunk

        result["success"] = True
        logger.info(
            f"[{test_name}] Response received ({len(result['chunks'])} chunks, "
            f"{len(result['response'])} chars)"
        )

    except TimeoutError:
        result["error"] = f"Timeout after {timeout}s"
        logger.error(f"[{test_name}] Timeout!")

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"[{test_name}] Error: {e}", exc_info=True)

    result["duration_seconds"] = asyncio.get_event_loop().time() - start_time
    return result


def get_results_summary(results_dir: Path) -> dict:
    """Get a summary of test results from the results directory.

    Args:
        results_dir: Path to results directory

    Returns:
        Dictionary with summary statistics
    """
    from collections import defaultdict

    if not results_dir.exists():
        return {"error": "Results directory not found"}

    results = list(results_dir.glob("*.json"))

    summary = {
        "total_tests": 0,
        "successful": 0,
        "failed": 0,
        "by_test": defaultdict(list),
        "latest_results": {},
    }

    for result_file in results:
        try:
            with open(result_file) as f:
                data = json.load(f)

            test_name = data.get("test_name", "unknown")
            success = data.get("success", False)

            summary["total_tests"] += 1
            if success:
                summary["successful"] += 1
            else:
                summary["failed"] += 1

            summary["by_test"][test_name].append(
                {
                    "file": result_file.name,
                    "success": success,
                    "timestamp": data.get("timestamp"),
                }
            )

        except Exception as e:
            logger.warning(f"Could not parse {result_file}: {e}")

    # Get latest result for each test
    for test_name, results in summary["by_test"].items():
        if results:
            latest = max(results, key=lambda x: x.get("timestamp", ""))
            summary["latest_results"][test_name] = latest

    return summary


def print_results_summary(results_dir: Path) -> None:
    """Print a formatted summary of test results.

    Args:
        results_dir: Path to results directory
    """
    summary = get_results_summary(results_dir)

    if "error" in summary:
        print(f"Error: {summary['error']}")
        return

    print("\n" + "=" * 60)
    print("INTEGRATION TEST RESULTS SUMMARY")
    print("=" * 60)
    print(f"Total Tests: {summary['total_tests']}")
    print(f"Successful:  {summary['successful']}")
    print(f"Failed:      {summary['failed']}")
    print()

    print("Latest Results by Test:")
    print("-" * 60)
    for test_name, result in summary["latest_results"].items():
        status = "✓" if result["success"] else "✗"
        print(f"{status} {test_name:20s} - {result['file']}")
    print("=" * 60)
    print(f"\nDetailed results in: {results_dir}")
    print()


if __name__ == "__main__":
    """Test the helper functions."""

    async def main():
        # Check if running
        running = await is_progenitor_running()
        print(f"Progenitor running: {running}")

        if not running:
            print("Waiting for Progenitor to start...")
            ready = await wait_for_progenitor(timeout=10.0)
            print(f"Progenitor ready: {ready}")

        # Print summary
        results_dir = Path(__file__).parent / "results"
        print_results_summary(results_dir)

    asyncio.run(main())
