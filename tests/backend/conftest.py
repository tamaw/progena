"""
Shared fixtures for backend tests.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.backend.alerts.service import clear_alerts
from src.backend.chat.models import AgentResponse


@pytest.fixture(autouse=True)
def cleanup_event_loop():
    """Clean up any pending event loop tasks after each test."""
    yield
    # Force cleanup of any pending tasks
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        return

    # Cancel all pending tasks
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending and not loop.is_running():
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))


@pytest.fixture(autouse=True)
def mock_progena_client():
    """Mock the get_client and get_sse_client to avoid connection issues in tests."""
    mock_client = AsyncMock()
    mock_client.send_message.return_value = AgentResponse(
        response="mocked response", reasoning=None, reasoning_tokens=None
    )
    mock_client.list_models.return_value = {"models": []}
    mock_client.is_running = False
    mock_client.start = AsyncMock()
    mock_client.stop = AsyncMock()

    with (
        patch("src.backend.integrations.runtime.get_sse_client", return_value=mock_client),
        patch("src.backend.lifecycle.state.client", None),
        patch("src.backend.lifecycle.state.config.telegram_enabled", False),
    ):
        yield


@pytest.fixture(autouse=True)
def reset_alert_store():
    """Ensure backend alert state does not leak between tests."""
    clear_alerts()
    yield
    clear_alerts()


@pytest.fixture(autouse=True)
def isolate_uvicorn_logging():
    """Keep real-server tests from leaking Uvicorn's global log configuration."""
    loggers = [logging.getLogger(name) for name in ("uvicorn", "uvicorn.error", "uvicorn.access")]
    original = [
        (logger.level, logger.handlers[:], logger.filters[:], logger.propagate, logger.disabled)
        for logger in loggers
    ]
    try:
        yield
    finally:
        for logger, (level, handlers, filters, propagate, disabled) in zip(
            loggers, original, strict=True
        ):
            logger.setLevel(level)
            logger.handlers = handlers
            logger.filters = filters
            logger.propagate = propagate
            logger.disabled = disabled


@pytest.fixture
def client():
    """Provide a TestClient for the FastAPI app."""
    from fastapi.testclient import TestClient

    from src.backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def app():
    """Get the FastAPI app instance."""
    from src.backend.main import app

    return app
