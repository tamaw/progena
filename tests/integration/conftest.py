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

    if run_integration and not skip_integration:
        return

    reason = (
        "Integration tests skipped by default; pass --run-integration to enable them"
        if not skip_integration
        else "Integration tests skipped (--skip-integration)"
    )
    skip_marker = pytest.mark.skip(reason=reason)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require a live Progenitor instance"
    )
    parser.addoption(
        "--skip-integration",
        action="store_true",
        default=False,
        help="Skip integration tests that require Progenitor to be running"
    )
