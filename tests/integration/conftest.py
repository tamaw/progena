"""
Conftest for integration tests.

This module provides pytest fixtures and configuration for integration tests.
It checks if Progenitor is running before tests execute.
"""

import asyncio
import logging

import pytest

from tests.integration.helpers import is_progenitor_running

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
    """Skip integration tests if Progenitor is not running."""
    # Check if we should skip integration tests
    skip_integration = config.getoption("--skip-integration", default=False)
    
    if skip_integration:
        skip_marker = pytest.mark.skip(reason="Integration tests skipped (--skip-integration)")
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
