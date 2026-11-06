"""
Shared fixtures for backend tests.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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
        # App lifespans must never connect a real channel (a developer .env
        # may hold live TELEGRAM_* credentials).
        patch("src.backend.channels.manager.ChannelManager.start", AsyncMock()),
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
def ui_stream():
    """Register one canonical-stream subscriber as client ``tab-1`` and yield its queue."""
    from src.backend.events import stream as events_stream

    queue: asyncio.Queue = asyncio.Queue()
    events_stream._ui_stream_subscribers[queue] = None
    events_stream._ui_stream_clients[queue] = "tab-1"
    try:
        yield queue
    finally:
        events_stream._ui_stream_subscribers.pop(queue, None)
        events_stream._ui_stream_clients.pop(queue, None)


class UiStreamTestClient:
    """Test client that supplies the active canonical-stream requester."""

    def __init__(self, client, client_id: str) -> None:
        self._client = client
        self._client_id = client_id

    def request(self, method, url, **kwargs):
        parsed = urlsplit(url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        params.update(kwargs.pop("params", {}) or {})
        params.setdefault("client_id", self._client_id)
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
        return self._client.request(method, target, params=params, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


@pytest.fixture
def ui_stream_client():
    """Register a canonical stream and return its requester identity."""
    from src.backend.events import stream as events_stream

    queues = [asyncio.Queue(), asyncio.Queue()]
    client_id = "tab-1"
    for queue, stream_client_id in zip(queues, (client_id, "tab"), strict=True):
        events_stream._ui_stream_subscribers[queue] = None
        events_stream._ui_stream_clients[queue] = stream_client_id
    try:
        yield client_id
    finally:
        for queue in queues:
            events_stream._ui_stream_subscribers.pop(queue, None)
            events_stream._ui_stream_clients.pop(queue, None)


@pytest.fixture
def client(ui_stream_client):
    """Provide a TestClient with an active canonical-stream requester."""
    from fastapi.testclient import TestClient

    from src.backend.main import app

    with TestClient(app) as test_client:
        yield UiStreamTestClient(test_client, ui_stream_client)


@pytest.fixture
def app():
    """Get the FastAPI app instance."""
    from src.backend.main import app

    return app
