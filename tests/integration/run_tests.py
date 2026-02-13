#!/usr/bin/env python3
"""
Run integration tests with proper setup and reporting.

This script:
1. Checks if Progenitor is running
2. Runs the integration tests
3. Displays a summary of results
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.integration.helpers import (
    is_progenitor_running,
    print_results_summary,
    wait_for_progenitor,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def check_progenitor() -> bool:
    """Check if Progenitor is running, wait if needed."""
    if await is_progenitor_running():
        logger.info("✓ Progenitor is running")
        return True

    logger.warning("Progenitor is not running")
    logger.info("Please start Progenitor before running integration tests:")
    logger.info("  python -m app.progenitor.main")
    logger.info("\nWaiting 30 seconds for Progenitor to start...")

    if await wait_for_progenitor(timeout=30.0):
        logger.info("✓ Progenitor is now running")
        return True

    logger.error("✗ Progenitor did not start in time")
    return False


def run_pytest() -> int:
    """Run pytest on integration tests."""
    import subprocess

    logger.info("Running integration tests...")

    result = subprocess.run(
        ["pytest", "tests/integration/test_live_ipc.py", "-v", "-m", "integration"],
        cwd=PROJECT_ROOT,
    )

    return result.returncode


def main() -> int:
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Progenitor Integration Test Runner")
    logger.info("=" * 60)

    # Check Progenitor
    if not asyncio.run(check_progenitor()):
        return 1

    # Run tests
    logger.info("")
    exit_code = run_pytest()

    # Show summary
    logger.info("")
    results_dir = Path(__file__).parent / "results"
    print_results_summary(results_dir)

    # Cleanup reminder
    logger.info("")
    logger.info("=" * 60)
    logger.info("Remember to stop Progenitor when done:")
    logger.info("  pwsh tests/integration/cleanup.ps1")
    logger.info("=" * 60)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
