"""
Tests for Backend.

Quick and dirty tests for the backend components.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.backend.client import ProgenitorClient
from app.backend.config import ChatServerConfig
from app.backend.ipc_client import start_ipc_client_with_retry
from app.backend.models import (
    ResponsePayload,
    ResponseRequest,
)


class TestChatServerConfig:
    """Tests for ChatServerConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ChatServerConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert config.debug is False
        assert config.progenitor_timeout == 300
        assert config.model_name == "progenitor-v1"

    def test_from_env(self, monkeypatch):
        """Test loading config from environment."""
        monkeypatch.setenv("CHAT_SERVER_PORT", "9000")
        monkeypatch.setenv("CHAT_SERVER_DEBUG", "true")
        monkeypatch.setenv("CHAT_SERVER_MODEL_NAME", "test-model")

        config = ChatServerConfig.from_env()
        assert config.port == 9000
        assert config.debug is True
        assert config.model_name == "test-model"


class TestResponseModels:
    """Tests for Responses API models."""

    def test_response_request_and_payload(self):
        """Test Responses API models."""
        req = ResponseRequest(input="Hello")
        assert req.model == "progenitor-v1"
        assert req.stream is False

        payload = ResponsePayload(
            id="resp-1",
            model="progenitor-v1",
            output=[],
        )
        assert payload.object == "response"
        assert payload.model == "progenitor-v1"


class TestProgenitorClient:
    """Tests for ProgenitorClient."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create a test config."""
        # Create a dummy script
        script = tmp_path / "dummy_progenitor.py"
        script.write_text("""
import sys
import json

# Simple echo server
for line in sys.stdin:
    data = json.loads(line)
    response = {"content": f"Echo: {data.get('content', '')}"}
    print(json.dumps(response), flush=True)
""")
        return ChatServerConfig(
            progenitor_script=script,
            progenitor_timeout=5,
        )

    @pytest.mark.asyncio
    async def test_client_init(self, config):
        """Test client initialization."""
        client = ProgenitorClient(config)
        assert client.is_running is False

    @pytest.mark.asyncio
    async def test_client_start_missing_script(self):
        """Test client fails with missing script."""
        config = ChatServerConfig(
            progenitor_script=Path("/nonexistent/script.py"),
        )
        client = ProgenitorClient(config)

        with pytest.raises(FileNotFoundError):
            await client.start()

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, config):
        """Test send_message auto-starts the client."""
        client = ProgenitorClient(config)

        # Mock the subprocess
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.readline = AsyncMock(return_value=b'{"content": "test response"}\n')
        mock_process.stderr = MagicMock()
        mock_process.stderr.readline = AsyncMock(return_value=b"")
        mock_process.returncode = None
        mock_process.pid = 12345
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_process),
        ):
            response = await client.send_message("hello")
            assert response == "test response"

        await client.stop()


class TestIPCEventForwardingStartup:
    """Tests for background IPC event-forwarding startup."""

    @pytest.mark.asyncio
    async def test_start_ipc_client_with_retry_retries_until_started(self):
        """Background startup should retry until the IPC client connects."""
        client = AsyncMock()
        client.start = AsyncMock(side_effect=[RuntimeError("offline"), None])

        sleep = AsyncMock()
        with patch("app.backend.ipc_client.asyncio.sleep", sleep):
            await start_ipc_client_with_retry(client, retry_delay=0.25)

        assert client.start.await_count == 2
        sleep.assert_awaited_once_with(0.25)


class TestChatServerAPI:
    """Tests for the FastAPI endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create a test client."""
        from fastapi.testclient import TestClient

        from app.backend.main import app

        return TestClient(app)

    def test_health_check(self, test_client):
        """Test health check endpoint."""
        response = test_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("healthy", "draining")  # Status values from actual endpoint

    def test_list_models(self, test_client):
        """Test models listing endpoint."""
        response = test_client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1

    def test_responses_no_input(self, test_client):
        """Test responses endpoint with empty input."""
        response = test_client.post(
            "/v1/responses",
            json={"model": "test", "input": ""},
        )
        # Returns 503 if client not initialized, or 400 if input empty
        assert response.status_code in (400, 503)

    def test_responses_endpoint(self, test_client):
        """Test unified responses endpoint (preferred)."""
        response = test_client.post(
            "/v1/responses",
            json={"model": "test", "input": ""},
        )
        assert response.status_code in (400, 503)

    def test_simple_chat_no_message(self, test_client):
        """Test simple chat with no message."""
        response = test_client.post("/chat", json={})
        # Returns 503 if client not initialized, 400 if no message, or 422 for Pydantic validation
        assert response.status_code in (400, 422, 503)
