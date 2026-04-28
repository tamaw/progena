"""
Helper utilities for integration tests.

Provides functions to check if Progenitor is running,
start it if needed, and wait for it to be ready.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.client import IPCClient

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9500
STARTUP_TIMEOUT = 30.0
CONNECTION_RETRIES = 10


async def is_progenitor_running(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> bool:
    """Check if Progenitor IPC server is running and accepting connections.

    Args:
        host: IPC server host
        port: IPC server port

    Returns:
        True if server is running and responsive, False otherwise
    """
    client = IPCClient(host=host, port=port, auto_reconnect=False)

    try:
        connected = await client.connect()
        if connected:
            # Send a simple heartbeat to verify it's working
            await asyncio.sleep(0.5)  # Give it time to be ready
            await client.disconnect()
            return True
        return False
    except Exception as e:
        logger.debug(f"Connection check failed: {e}")
        return False


async def wait_for_progenitor(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = STARTUP_TIMEOUT,
    retry_interval: float = 2.0,
) -> bool:
    """Wait for Progenitor IPC server to become available.

    Args:
        host: IPC server host
        port: IPC server port
        timeout: Maximum time to wait in seconds
        retry_interval: Time between connection attempts

    Returns:
        True if server became available, False if timeout
    """
    logger.info(f"Waiting for Progenitor at {host}:{port}...")

    start_time = asyncio.get_event_loop().time()
    attempts = 0

    while True:
        attempts += 1
        elapsed = asyncio.get_event_loop().time() - start_time

        if elapsed > timeout:
            logger.error(f"Timeout waiting for Progenitor after {attempts} attempts")
            return False

        if await is_progenitor_running(host, port):
            logger.info(f"Progenitor is ready (attempt {attempts}, {elapsed:.1f}s)")
            return True

        logger.debug(f"Attempt {attempts}: Not ready yet, retrying in {retry_interval}s...")
        await asyncio.sleep(retry_interval)


def get_results_summary(results_dir: Path) -> dict:
    """Get a summary of test results from the results directory.

    Args:
        results_dir: Path to results directory

    Returns:
        Dictionary with summary statistics
    """
    import json
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
