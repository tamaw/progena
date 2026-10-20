"""
Tests for Backend.

Quick and dirty tests for the backend components.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.backend.alerts import AlertInfo
from app.backend.client import ProgenitorClient
from app.backend.config import ChatServerConfig
from app.backend.main import _relay_alert
from app.backend.models import AgentResponse
from app.backend.sse_client import start_sse_client_with_retry


class TestChatServerConfig:
    """Tests for ChatServerConfig."""

    def test_from_env(self, monkeypatch):
        """Test loading config from environment."""
        monkeypatch.setenv("CHAT_SERVER_PORT", "9000")
        monkeypatch.setenv("CHAT_SERVER_DEBUG", "true")
        monkeypatch.setenv("CHAT_SERVER_MODEL_NAME", "test-model")

        config = ChatServerConfig.from_env()
        assert config.port == 9000
        assert config.debug is True
        assert config.model_name == "test-model"


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
            assert response.response == "test response"

        await client.stop()


class TestSSEEventForwardingStartup:
    """Tests for background SSE event-forwarding startup."""

    @pytest.mark.asyncio
    async def test_start_sse_client_with_retry_retries_until_started(self):
        """Background startup should retry until the SSE client connects."""
        client = AsyncMock()
        client.start = AsyncMock(side_effect=[RuntimeError("offline"), None])

        sleep = AsyncMock()
        with patch("app.backend.sse_client.asyncio.sleep", sleep):
            await start_sse_client_with_retry(client, retry_delay=0.25)

        assert client.start.await_count == 2
        sleep.assert_awaited_once_with(0.25)


class TestAlertRelay:
    """New alert documents are relayed to the user through Progenitor."""

    def _alert(self, **overrides):
        return AlertInfo(
            **{
                "id": "alert-1",
                "type": "warning",
                "title": "Build failed",
                "message": "Tests failed in CI",
                "timestamp": 123.0,
                "urgent": True,
                "requires_response": False,
                **overrides,
            }
        )

    @pytest.mark.asyncio
    async def test_relay_sends_one_summary_turn(self):
        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message = AsyncMock(
            return_value=AgentResponse(response="sent", reasoning=None, reasoning_tokens=None)
        )

        await _relay_alert(progenitor, self._alert(id="alert-42"))

        progenitor.start.assert_not_awaited()
        progenitor.send_message.assert_awaited_once()
        assert progenitor.send_message.await_args.kwargs["request_id"] == "alert-summary-alert-42"

    @pytest.mark.asyncio
    async def test_relay_starts_client_when_needed(self):
        progenitor = AsyncMock()
        progenitor.is_running = False
        progenitor.send_message = AsyncMock(
            return_value=AgentResponse(response="sent", reasoning=None, reasoning_tokens=None)
        )

        await _relay_alert(progenitor, self._alert())

        progenitor.start.assert_awaited_once()


class TestChatServerAPI:
    """Tests for the FastAPI endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create a test client."""
        from fastapi.testclient import TestClient

        from app.backend.main import app

        return TestClient(app)

    def test_ui_responses_no_input(self, test_client):
        """Canonical browser generation rejects an empty prompt."""
        response = test_client.post(
            "/api/ui/responses",
            json={"model": "test", "input": ""},
        )
        assert response.status_code == 400
