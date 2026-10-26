"""
Tests for OBS-001: Request ID Correlation.

Validates that request IDs are generated, propagated, and returned correctly.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    """Get the FastAPI app instance."""
    from src.backend.main import app

    return app


class TestRequestIdMiddleware:
    """Test request ID middleware behavior."""

    def test_request_id_generated_when_not_provided(self, app):
        """Test that a request ID is generated when not provided."""
        with TestClient(app) as client:
            response = client.get("/api/ui/widgets")

        assert "X-Operation-Id" in response.headers
        assert response.headers["X-Operation-Id"].startswith("req-")

    def test_request_id_preserved_when_provided(self, app):
        """Test that provided request ID is preserved."""
        custom_id = "custom-request-12345"

        with TestClient(app) as client:
            response = client.get("/api/ui/widgets", headers={"X-Operation-Id": custom_id})

        assert response.headers["X-Operation-Id"] == custom_id

    def test_finite_response_exposes_timing_metric(self, app):
        """Finite responses expose a browser-visible server timing measurement."""
        with TestClient(app) as client:
            response = client.get("/api/ui/widgets")

        assert "Server-Timing" in response.headers
        assert response.headers["Server-Timing"].startswith("app;dur=")
        assert float(response.headers["X-Response-Time-Ms"]) >= 0

    def test_large_response_is_compressed(self, app):
        """Large finite responses negotiate gzip compression."""
        with TestClient(app) as client:
            response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

        assert response.headers.get("content-encoding") == "gzip"
        vary = {token.strip() for token in response.headers.get("vary", "").lower().split(",")}
        assert "accept-encoding" in vary


class TestClientRequestIdPropagation:
    """Test that request ID is passed to the Progena client."""

    @pytest.mark.asyncio
    async def test_send_message_accepts_request_id(self):
        """Test that send_message accepts request_id parameter."""
        from src.backend.config import ChatServerConfig
        from src.backend.integrations.subprocess import ProgenaClient

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenaClient(config)

        # Mock the process
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline = AsyncMock(return_value=b'{"content": "test response"}\n')
        mock_process.returncode = None  # Process is running

        client._process = mock_process
        client._started = True
        client._last_heartbeat = 0  # Initialize heartbeat
        config.progena_timeout = 30

        # Call with request_id
        await client.send_message("test message", request_id="req-abc123")

        # Verify the payload included request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert payload["request_id"] == "req-abc123"
        assert payload["content"] == "test message"

    @pytest.mark.asyncio
    async def test_send_message_without_request_id(self):
        """Test that send_message works without request_id."""
        from src.backend.config import ChatServerConfig
        from src.backend.integrations.subprocess import ProgenaClient

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenaClient(config)

        # Mock the process
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline = AsyncMock(return_value=b'{"content": "test response"}\n')
        mock_process.returncode = None  # Process is running

        client._process = mock_process
        client._started = True
        client._last_heartbeat = 0  # Initialize heartbeat
        config.progena_timeout = 30

        # Call without request_id
        await client.send_message("test message")

        # Verify the payload does not include request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert "request_id" not in payload

    @pytest.mark.asyncio
    async def test_send_message_stream_events_accepts_request_id(self):
        """Test that send_message_stream_events accepts request_id parameter."""
        from src.backend.config import ChatServerConfig
        from src.backend.integrations.subprocess import ProgenaClient

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenaClient(config)

        # Mock the process
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()

        # Create async iterator for streaming response
        async def mock_readline():
            return b'{"type": "stream_chunk", "content": "test", "is_final": true}\n'

        mock_process.stdout.readline = mock_readline

        client._process = mock_process
        client._started = True
        config.progena_timeout = 30

        # Call with request_id and consume the stream
        events = []
        async for event in client.send_message_stream_events(
            "test message", request_id="req-xyz789"
        ):
            events.append(event)

        # Verify the payload included request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert payload["request_id"] == "req-xyz789"
        assert payload["stream"] is True
        assert events == [
            {
                "type": "stream_chunk",
                "content": "test",
                "is_final": True,
                "request_id": "req-xyz789",
            }
        ]


class TestRequestIdInLogs:
    """Test that request ID appears in log messages."""

    def test_request_id_logged_on_request(self, caplog):
        """Test that request ID is logged for incoming requests."""
        import logging

        from src.backend.main import app

        with caplog.at_level(logging.INFO), TestClient(app) as client:
            response = client.get("/api/ui/widgets")

        request_id = response.headers["X-Operation-Id"]

        # Check that the request ID appears in the logs
        assert any(request_id in record.message for record in caplog.records)


class TestRequestIdHandlerIntegration:
    """Finite application acknowledgements preserve client operation IDs."""

    def test_finite_handler_echoes_supplied_request_id(self, app):
        request_id = "finite-operation-id"
        with TestClient(app) as client:
            response = client.get(
                "/api/ui/widgets",
                headers={"X-Operation-Id": request_id},
            )

        assert response.status_code == 202
        assert response.headers["X-Operation-Id"] == request_id
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["widgets.updated"],
        }
