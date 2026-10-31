"""
Tests for Backend.

Quick and dirty tests for the backend components.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.backend.alerts.relay as alerts_relay
from src.backend.alerts.service import AlertInfo
from src.backend.chat.models import AgentResponse
from src.backend.config import ChatServerConfig
from src.backend.integrations.runtime import start_sse_client_with_retry
from src.backend.integrations.subprocess import ProgenaClient


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


class TestProgenaClient:
    """Tests for ProgenaClient."""

    @pytest.fixture
    def config(self, tmp_path):
        """Create a test config."""
        # Create a dummy script
        script = tmp_path / "dummy_progena.py"
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
            progena_script=script,
            progena_timeout=5,
        )

    @pytest.mark.asyncio
    async def test_client_start_missing_script(self):
        """Test client fails with missing script."""
        config = ChatServerConfig(
            progena_script=Path("/nonexistent/script.py"),
        )
        client = ProgenaClient(config)

        with pytest.raises(FileNotFoundError):
            await client.start()

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, config):
        """Test send_message auto-starts the client."""
        client = ProgenaClient(config)

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

    @pytest.mark.asyncio
    async def test_stream_adapter_emits_lifecycle_for_consecutive_submissions(self, config):
        client = ProgenaClient(config)
        process = MagicMock()
        process.stdin = MagicMock()
        process.stdin.drain = AsyncMock()
        process.stdout = MagicMock()
        process.stdout.readline = AsyncMock(
            side_effect=[
                b'{"content":"Echo: first"}\n',
                b'{"content":"Echo: second"}\n',
            ]
        )
        process.stderr = MagicMock()
        process.stderr.readline = AsyncMock(return_value=b"")
        process.returncode = None
        process.pid = 12345
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)):
            try:
                first = [
                    event
                    async for event in client.send_message_stream_events(
                        "first", request_id="first-request"
                    )
                ]
                second = [
                    event
                    async for event in client.send_message_stream_events(
                        "second", request_id="second-request"
                    )
                ]
            finally:
                await client.stop()

        assert first[-1] == {
            "type": "response_terminal",
            "status": "completed",
            "request_id": "first-request",
        }
        assert second[-1] == {
            "type": "response_terminal",
            "status": "completed",
            "request_id": "second-request",
        }
        assert [event["type"] for event in first] == [
            "response_started",
            "stream_chunk",
            "response_terminal",
        ]
        assert [event["type"] for event in second] == [
            "response_started",
            "stream_chunk",
            "response_terminal",
        ]


class TestSSEEventForwardingStartup:
    """Tests for background SSE event-forwarding startup."""

    @pytest.mark.asyncio
    async def test_start_sse_client_with_retry_retries_until_started(self):
        """Background startup should retry until the SSE client connects."""
        client = AsyncMock()
        client.start = AsyncMock(side_effect=[RuntimeError("offline"), None])

        sleep = AsyncMock()
        with patch("src.backend.integrations.runtime.asyncio.sleep", sleep):
            await start_sse_client_with_retry(client, retry_delay=0.25)

        assert client.start.await_count == 2
        sleep.assert_awaited_once_with(0.25)


class TestAlertRelay:
    """New alert documents are relayed to the user through Progena."""

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
        progena = AsyncMock()
        progena.is_running = True
        progena.send_message = AsyncMock(
            return_value=AgentResponse(response="sent", reasoning=None, reasoning_tokens=None)
        )

        await alerts_relay._relay_alert(progena, self._alert(id="alert-42"))

        progena.start.assert_not_awaited()
        progena.send_message.assert_awaited_once()
        assert progena.send_message.await_args.kwargs["request_id"] == "alert-summary-alert-42"

    @pytest.mark.asyncio
    async def test_relay_starts_client_when_needed(self):
        progena = AsyncMock()
        progena.is_running = False
        progena.send_message = AsyncMock(
            return_value=AgentResponse(response="sent", reasoning=None, reasoning_tokens=None)
        )

        await alerts_relay._relay_alert(progena, self._alert())

        progena.start.assert_awaited_once()


class TestChatServerAPI:
    """Tests for the FastAPI endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create a test client."""
        from fastapi.testclient import TestClient

        from src.backend.main import app

        return TestClient(app)

    def test_ui_responses_no_input(self, test_client):
        """Canonical browser generation rejects an empty prompt."""
        response = test_client.post(
            "/api/ui/responses",
            json={"model": "test", "input": ""},
        )
        assert response.status_code == 400
