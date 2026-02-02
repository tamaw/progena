"""
Tests for Telegram Client Integration.

Tests the TelegramClient class, rate limiting, message chunking,
and integration with the backend event system.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import deque

from app.backend.telegram_client import (
    TelegramClient,
    TelegramConfig,
    TelegramRateLimiter,
    init_telegram_client,
    stop_telegram_client,
    get_telegram_client,
)
from app.backend.config import ChatServerConfig


class TestTelegramConfig:
    """Tests for TelegramConfig."""

    def test_from_chat_config(self):
        """Test extracting Telegram config from ChatServerConfig."""
        chat_config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="123456:ABC-DEF",
            telegram_chat_id="987654321",
            telegram_api_timeout=60,
            telegram_max_retries=5,
            telegram_retry_delay=2.0,
            telegram_max_message_length=2048,
            telegram_rate_limit_per_minute=20,
        )

        tg_config = TelegramConfig.from_chat_config(chat_config)

        assert tg_config.bot_token == "123456:ABC-DEF"
        assert tg_config.chat_id == "987654321"
        assert tg_config.api_timeout == 60
        assert tg_config.max_retries == 5
        assert tg_config.retry_delay == 2.0
        assert tg_config.max_message_length == 2048
        assert tg_config.rate_limit_per_minute == 20


class TestTelegramRateLimiter:
    """Tests for TelegramRateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        """Test acquiring when under rate limit."""
        limiter = TelegramRateLimiter(max_per_minute=30)

        # Should be able to acquire without waiting
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_acquire_at_limit(self):
        """Test acquiring when at rate limit."""
        limiter = TelegramRateLimiter(max_per_minute=2)

        # Acquire twice (at limit)
        await limiter.acquire()
        await limiter.acquire()

        # Third acquire should require waiting
        wait_time = await limiter.acquire()
        assert wait_time > 0.0

    @pytest.mark.asyncio
    async def test_sliding_window(self):
        """Test that old timestamps expire from the window."""
        limiter = TelegramRateLimiter(max_per_minute=2)

        # Add old timestamps manually
        old_time = asyncio.get_event_loop().time() - 120  # 2 minutes ago
        limiter._timestamps.append(old_time)
        limiter._timestamps.append(old_time)

        # Should still be able to acquire since old ones expired
        wait_time = await limiter.acquire()
        assert wait_time == 0.0


class TestTelegramClient:
    """Tests for TelegramClient."""

    @pytest.fixture
    def config(self):
        """Create test Telegram config."""
        return TelegramConfig(
            bot_token="123456:ABC-DEF",
            chat_id="987654321",
            api_timeout=10,
            max_retries=3,
            retry_delay=0.1,  # Short for testing
            max_message_length=4096,
            rate_limit_per_minute=30,
        )

    @pytest.fixture
    def client(self, config):
        """Create test Telegram client."""
        return TelegramClient(config)

    def test_split_message_short(self, client):
        """Test that short messages are not split."""
        text = "Hello, world!"
        chunks = client._split_message(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_message_long(self, client):
        """Test splitting long messages."""
        # Create a message longer than max length
        client.config.max_message_length = 100
        text = "A" * 250

        chunks = client._split_message(text)
        assert len(chunks) == 3
        assert all(len(chunk) <= 100 for chunk in chunks)
        # Verify all content is preserved
        assert "".join(chunks) == text

    def test_split_message_at_newline(self, client):
        """Test that messages are split at newlines when possible."""
        client.config.max_message_length = 100
        text = "Line one\nLine two that is a bit longer\nLine three"

        # Manually create a case where split should happen at newline
        long_text = "A" * 50 + "\n" + "B" * 60

        chunks = client._split_message(long_text)
        assert len(chunks) == 2
        assert chunks[0].endswith("A")  # Split at newline
        assert chunks[1].startswith("B")

    @pytest.mark.asyncio
    async def test_start_without_config(self, client):
        """Test that start handles missing config gracefully."""
        client.config.bot_token = ""

        await client.start()
        assert not client._started

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, client):
        """Test sending message when client not started."""
        result = await client.send_message("Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_empty(self, client):
        """Test sending empty message."""
        client._started = True
        client._session = MagicMock()

        result = await client.send_message("")
        assert result is True  # Empty messages are skipped successfully

    @pytest.mark.asyncio
    async def test_api_call_success(self, client):
        """Test successful API call."""
        client._started = True

        # Mock aiohttp session
        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": True,
            "result": {"username": "test_bot"}
        })

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None)
        ))
        client._session = mock_session

        result = await client._api_call("getMe")
        assert result == {"username": "test_bot"}

    @pytest.mark.asyncio
    async def test_api_call_rate_limited(self, client):
        """Test API call with rate limit error."""
        client._started = True

        # First call returns rate limit, second succeeds
        call_count = 0

        async def mock_json():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 0.1}  # Short for testing
                }
            return {
                "ok": True,
                "result": {"message_id": 123}
            }

        mock_response = AsyncMock()
        mock_response.json = mock_json

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)
        client._session = mock_session

        result = await client._api_call("sendMessage", {"chat_id": "123", "text": "test"})
        assert result == {"message_id": 123}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_api_call_unauthorized(self, client):
        """Test API call with unauthorized error (no retry)."""
        client._started = True

        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={
            "ok": False,
            "error_code": 401,
            "description": "Unauthorized"
        })

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)
        client._session = mock_session

        result = await client._api_call("sendMessage", {"chat_id": "123", "text": "test"})
        assert result is None  # No retry for auth errors


class TestTelegramClientIntegration:
    """Integration tests for Telegram client with backend."""

    @pytest.mark.asyncio
    async def test_init_telegram_client_disabled(self):
        """Test that client is not created when disabled."""
        config = ChatServerConfig(telegram_enabled=False)

        client = await init_telegram_client(config)
        assert client is None

    @pytest.mark.asyncio
    async def test_init_telegram_client_missing_token(self):
        """Test that client is not created with missing token."""
        config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="",
            telegram_chat_id="123456",
        )

        client = await init_telegram_client(config)
        assert client is None

    @pytest.mark.asyncio
    async def test_init_and_stop(self):
        """Test initializing and stopping the global client."""
        config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="123456:ABC",
            telegram_chat_id="987654",
        )

        # Mock the start method to not actually connect
        with patch.object(TelegramClient, 'start', new_callable=AsyncMock):
            client = await init_telegram_client(config)
            assert client is not None
            assert get_telegram_client() is client

            await stop_telegram_client()
            assert get_telegram_client() is None


class TestSubscriberTracking:
    """Tests for subscriber tracking in ui_routes."""

    def test_has_active_chat_subscribers_empty(self):
        """Test when no subscribers are connected."""
        from app.backend.ui_routes import has_active_chat_subscribers, _chat_subscribers

        # Clear subscribers for test
        original = _chat_subscribers.copy()
        _chat_subscribers.clear()

        try:
            assert has_active_chat_subscribers() is False
        finally:
            _chat_subscribers.extend(original)

    def test_has_active_chat_subscribers_with_subscriber(self):
        """Test when subscribers are connected."""
        from app.backend.ui_routes import has_active_chat_subscribers, _chat_subscribers

        # Add a mock subscriber
        original = _chat_subscribers.copy()
        _chat_subscribers.clear()
        _chat_subscribers.append(asyncio.Queue())

        try:
            assert has_active_chat_subscribers() is True
        finally:
            _chat_subscribers.clear()
            _chat_subscribers.extend(original)

    def test_get_subscriber_counts(self):
        """Test getting subscriber counts."""
        from app.backend.ui_routes import get_subscriber_counts

        counts = get_subscriber_counts()
        assert "chat" in counts
        assert "status" in counts
        assert "legacy" in counts
        assert "total" in counts


class TestTelegramRoutes:
    """Tests for Telegram API routes."""

    @pytest.fixture
    def client(self):
        """Create test FastAPI client."""
        from fastapi.testclient import TestClient
        from app.backend.main import app
        return TestClient(app)

    def test_telegram_status_disabled(self, client):
        """Test status endpoint when Telegram is disabled."""
        # This test depends on the actual config which has Telegram disabled by default
        response = client.get("/api/ui/telegram/status")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "connected" in data
        assert "will_forward_to_telegram" in data

    def test_telegram_test_disabled(self, client):
        """Test test endpoint when Telegram is disabled."""
        response = client.post("/api/ui/telegram/test")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not enabled" in data["message"] or "not configured" in data["message"]
