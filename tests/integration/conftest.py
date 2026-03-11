"""
Conftest for integration tests.

This module provides pytest fixtures and configuration for integration tests.
It checks if Progenitor is running before tests execute.
"""

import asyncio
import logging

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as an integration test that requires a running Progenitor instance"
    )


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless they were explicitly requested."""
    run_integration = config.getoption("--run-integration", default=False)
    skip_integration = config.getoption("--skip-integration", default=False)

    skip_reason = None
    if skip_integration:
        skip_reason = "Integration tests skipped (--skip-integration)"
    elif not run_integration:
        skip_reason = "Integration tests skipped by default (use --run-integration)"

    if skip_reason is None:
        return

    skip_marker = pytest.mark.skip(reason=skip_reason)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--skip-integration",
        action="store_true",
        default=False,
        help="Skip integration tests that require Progenitor to be running"
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require a live Progenitor instance"
    )
