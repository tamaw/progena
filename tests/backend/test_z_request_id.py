"""
Tests for OBS-001: Request ID Correlation.

Validates that request IDs are generated, propagated, and returned correctly.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
import json


@pytest.fixture
def app():
    """Get the FastAPI app instance."""
    from app.backend.main import app
    return app


class TestRequestIdMiddleware:
    """Test request ID middleware behavior."""

    def test_request_id_generated_when_not_provided(self, app):
        """Test that a request ID is generated when not provided."""
        with TestClient(app) as client:
            response = client.get("/health")

        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"].startswith("req-")

    def test_request_id_preserved_when_provided(self, app):
        """Test that provided request ID is preserved."""
        custom_id = "custom-request-12345"

        with TestClient(app) as client:
            response = client.get("/health", headers={"X-Request-ID": custom_id})

        assert response.headers["X-Request-ID"] == custom_id

    def test_request_id_format(self, app):
        """Test that generated request ID has expected format."""
        with TestClient(app) as client:
            response = client.get("/health")

        request_id = response.headers["X-Request-ID"]
        # Format should be req-{12 hex chars}
        assert request_id.startswith("req-")
        assert len(request_id) == 16  # "req-" (4) + 12 hex chars


class TestClientRequestIdPropagation:
    """Test that request ID is passed to the Progenitor client."""

    @pytest.mark.asyncio
    async def test_send_message_accepts_request_id(self):
        """Test that send_message accepts request_id parameter."""
        from app.backend.client import ProgenitorClient
        from app.backend.config import ChatServerConfig

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenitorClient(config)

        # Mock the process
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline = AsyncMock(
            return_value=b'{"content": "test response"}\n'
        )
        mock_process.returncode = None  # Process is running

        client._process = mock_process
        client._started = True
        client._last_heartbeat = 0  # Initialize heartbeat
        config.progenitor_timeout = 30

        # Call with request_id
        result = await client.send_message("test message", request_id="req-abc123")

        # Verify the payload included request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert payload["request_id"] == "req-abc123"
        assert payload["content"] == "test message"

    @pytest.mark.asyncio
    async def test_send_message_without_request_id(self):
        """Test that send_message works without request_id."""
        from app.backend.client import ProgenitorClient
        from app.backend.config import ChatServerConfig

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenitorClient(config)

        # Mock the process
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline = AsyncMock(
            return_value=b'{"content": "test response"}\n'
        )
        mock_process.returncode = None  # Process is running

        client._process = mock_process
        client._started = True
        client._last_heartbeat = 0  # Initialize heartbeat
        config.progenitor_timeout = 30

        # Call without request_id
        result = await client.send_message("test message")

        # Verify the payload does not include request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert "request_id" not in payload

    @pytest.mark.asyncio
    async def test_send_message_stream_accepts_request_id(self):
        """Test that send_message_stream accepts request_id parameter."""
        from app.backend.client import ProgenitorClient
        from app.backend.config import ChatServerConfig

        config = MagicMock(spec=ChatServerConfig)
        client = ProgenitorClient(config)

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
        config.progenitor_timeout = 30

        # Call with request_id and consume the stream
        chunks = []
        async for chunk in client.send_message_stream("test message", request_id="req-xyz789"):
            chunks.append(chunk)

        # Verify the payload included request_id
        call_args = mock_process.stdin.write.call_args[0][0]
        payload = json.loads(call_args.decode().strip())

        assert payload["request_id"] == "req-xyz789"
        assert payload["stream"] is True


class TestRequestIdInLogs:
    """Test that request ID appears in log messages."""

    def test_request_id_logged_on_request(self, caplog):
        """Test that request ID is logged for incoming requests."""
        import logging
        from app.backend.main import app

        with caplog.at_level(logging.INFO):
            with TestClient(app) as client:
                response = client.get("/health")

        request_id = response.headers["X-Request-ID"]

        # Check that the request ID appears in the logs
        assert any(request_id in record.message for record in caplog.records)


class TestRequestIdHandlerIntegration:
    """Test request ID integration in API handlers."""

    def test_responses_endpoint_uses_request_state(self):
        """Verify /v1/responses handler accesses request.state.request_id."""
        import app.backend.main as main_module

        with open(main_module.__file__, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check that the handler gets request_id from state
        assert 'request.state' in content
        assert 'request_id' in content

    def test_chat_completions_endpoint_uses_request_state(self):
        """Verify /v1/responses handler accesses request.state.request_id."""
        import app.backend.main as main_module

        with open(main_module.__file__, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check that the handler gets request_id from state
        assert 'getattr(request.state, "request_id"' in content

    def test_stream_response_unified_accepts_request_id(self):
        """Verify stream_response_unified function accepts request_id parameter."""
        import app.backend.main as main_module

        with open(main_module.__file__, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check function signature
        assert 'def stream_response_unified(' in content
        assert 'request_id: str | None = None' in content
