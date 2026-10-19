"""
Tests for Telegram Client Integration.

Tests the TelegramClient class, rate limiting, message chunking,
polling, command handling, and integration with the backend event system.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.backend.config import ChatServerConfig
from app.backend.document_store import PROGENITOR_STATE_PREFIX, InMemoryDocumentStore
from app.backend.models import AgentResponse
from app.backend.telegram_client import (
    TelegramClient,
    TelegramConfig,
    TelegramDraftSession,
    TelegramDraftStreamingError,
    TelegramImage,
    TelegramMediaError,
    TelegramRateLimiter,
    get_telegram_client,
    init_telegram_client,
    stop_telegram_client,
)


async def _async_iter(*chunks: str):
    """Yield test chunks as an async iterator."""
    for chunk in chunks:
        yield chunk


async def _async_event_iter(*items):
    """Yield test stream events as an async iterator."""
    for item in items:
        yield item


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
        assert tg_config.draft_update_interval_ms == 1000


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
            retry_delay=0.1,
            max_message_length=100,  # Small for testing chunking
            rate_limit_per_minute=30,
            draft_update_interval_ms=1000,
        )

    @pytest.fixture
    def client(self, config):
        """Create TelegramClient for testing."""
        return TelegramClient(config)

    def test_split_message_short(self, client):
        """Test that short messages aren't split."""
        text = "Hello, world!"
        chunks = client._split_message(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_split_message_long(self, client):
        """Test that long messages are split."""
        text = "A" * 250  # Longer than max_message_length (100)
        chunks = client._split_message(text)
        assert len(chunks) == 3
        assert all(len(c) <= 100 for c in chunks)

    def test_split_message_at_newline(self, client):
        """Test that messages are split at newlines when possible."""
        text = "A" * 50 + "\n" + "B" * 60
        chunks = client._split_message(text)
        assert len(chunks) == 2
        assert "A" in chunks[0]
        assert "B" in chunks[1]

    @pytest.mark.asyncio
    async def test_start_without_config(self):
        """Test that start() handles missing config gracefully."""
        config = TelegramConfig(
            bot_token="",  # Empty
            chat_id="",
            api_timeout=10,
            max_retries=3,
            retry_delay=0.1,
            max_message_length=4096,
            rate_limit_per_minute=30,
            draft_update_interval_ms=1000,
        )
        client = TelegramClient(config)
        await client.start()
        assert client._started is False

    @pytest.mark.asyncio
    async def test_send_message_not_started(self, client):
        """Test that send_message returns False when not started."""
        result = await client.send_message("Hello")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_message_empty(self, client):
        """Test that empty messages are handled gracefully."""
        client._started = True
        client._session = MagicMock()
        result = await client.send_message("   ")
        assert result is True

    @pytest.mark.asyncio
    async def test_api_call_success(self, client):
        """Test successful API call."""
        client._started = True

        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 123}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        client._session = mock_session

        result = await client._api_call("sendMessage", {"chat_id": "123", "text": "Hello"})

        assert result == {"message_id": 123}

    @pytest.mark.asyncio
    async def test_api_call_rate_limited(self, client):
        """Test API call with rate limit (429) response."""
        client._started = True

        # First call returns 429, second succeeds
        responses = [
            {"ok": False, "error_code": 429, "parameters": {"retry_after": 0.1}},
            {"ok": True, "result": {"message_id": 123}},
        ]
        call_count = [0]

        async def mock_json():
            result = responses[call_count[0]]
            call_count[0] = min(call_count[0] + 1, len(responses) - 1)
            return result

        mock_response = AsyncMock()
        mock_response.json = mock_json
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        client._session = mock_session

        result = await client._api_call("sendMessage", {"chat_id": "123", "text": "Hello"})

        assert result == {"message_id": 123}

    @pytest.mark.asyncio
    async def test_api_call_unauthorized(self, client):
        """Test API call with unauthorized (401) response."""
        client._started = True

        mock_response = AsyncMock()
        mock_response.json = AsyncMock(
            return_value={"ok": False, "error_code": 401, "description": "Unauthorized"}
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        client._session = mock_session

        result = await client._api_call("sendMessage", {"chat_id": "123", "text": "Hello"})

        assert result is None

    @pytest.mark.asyncio
    async def test_stream_agent_response_to_chat_uses_drafts_then_commits(self, client):
        """Test throttled draft streaming followed by final commit."""
        client._started = True
        client._session = MagicMock()

        calls: list[tuple[str, dict]] = []

        async def mock_api(method, params=None, retry=True):
            calls.append((method, params or {}))
            return {"ok": True}

        with patch.object(client, "_api_call", side_effect=mock_api):
            streamed_chars = await client.stream_agent_response_to_chat(
                "111222333",
                _async_iter("Hello", " world"),
            )

        assert streamed_chars == len("Hello world")
        assert [method for method, _ in calls] == ["sendMessageDraft", "sendMessage"]
        assert calls[-1][1]["text"] == "🤖 Progenitor Agent:\n\nHello world"

    @pytest.mark.asyncio
    async def test_stream_agent_response_to_chat_splits_long_output(self, client):
        """Test that long streamed output rolls into multiple committed messages."""
        client._started = True
        client._session = MagicMock()

        calls: list[tuple[str, dict]] = []

        async def mock_api(method, params=None, retry=True):
            calls.append((method, params or {}))
            return {"ok": True}

        long_text = "A" * 160
        with patch.object(client, "_api_call", side_effect=mock_api):
            streamed_chars = await client.stream_agent_response_to_chat(
                "111222333",
                _async_iter(long_text),
            )

        assert streamed_chars == len(long_text)
        send_methods = [method for method, _ in calls]
        assert send_methods.count("sendMessageDraft") <= 1
        assert send_methods.count("sendMessage") == 2

    @pytest.mark.asyncio
    async def test_stream_agent_response_to_chat_can_disable_draft_throttle(self, client):
        """Test draft updates can be emitted for each chunk when interval is disabled."""
        client._started = True
        client._session = MagicMock()
        client.config.draft_update_interval_ms = 0

        calls: list[tuple[str, dict]] = []

        async def mock_api(method, params=None, retry=True):
            calls.append((method, params or {}))
            return {"ok": True}

        with patch.object(client, "_api_call", side_effect=mock_api):
            streamed_chars = await client.stream_agent_response_to_chat(
                "111222333",
                _async_iter("Hello", " world"),
            )

        assert streamed_chars == len("Hello world")
        assert [method for method, _ in calls] == [
            "sendMessageDraft",
            "sendMessageDraft",
            "sendMessage",
        ]

    @pytest.mark.asyncio
    async def test_stream_agent_response_to_chat_raises_on_draft_failure(self, client):
        """Test that draft streaming failures are surfaced explicitly."""
        client._started = True
        client._session = MagicMock()

        async def mock_api(method, params=None, retry=True):
            if method == "sendMessageDraft":
                return None
            return {"ok": True}

        with (
            patch.object(client, "_api_call", side_effect=mock_api),
            pytest.raises(TelegramDraftStreamingError),
        ):
            await client.stream_agent_response_to_chat(
                "111222333",
                _async_iter("Hello"),
            )

    def test_should_flush_draft_respects_interval_across_segments(self, client):
        """Test that a new segment does not bypass the global draft throttle window."""
        session = TelegramDraftSession(chat_id="111222333", draft_id=123)
        session.last_draft_text = ""
        session.last_draft_update_at = 10.0

        assert (
            client._should_flush_draft(
                session,
                "🤖 Progenitor Agent:\n\nnext segment",
                now=10.5,
            )
            is False
        )

        assert (
            client._should_flush_draft(
                session,
                "🤖 Progenitor Agent:\n\nnext segment",
                now=11.0,
            )
            is True
        )

    def test_should_flush_draft_allows_first_update(self, client):
        """Test that the first draft update is sent immediately."""
        session = TelegramDraftSession(chat_id="111222333", draft_id=123)

        assert (
            client._should_flush_draft(
                session,
                "🤖 Progenitor Agent:\n\nhello",
                now=0.5,
            )
            is True
        )


class TestTelegramClientIntegration:
    """Integration tests for Telegram client lifecycle."""

    @pytest.mark.asyncio
    async def test_init_telegram_client_disabled(self):
        """Test that init returns None when Telegram is disabled."""
        config = ChatServerConfig(telegram_enabled=False)
        result = await init_telegram_client(config)
        assert result is None

    @pytest.mark.asyncio
    async def test_init_telegram_client_missing_token(self):
        """Test that init returns None when token is missing."""
        config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="",
            telegram_chat_id="123456",
        )
        result = await init_telegram_client(config)
        assert result is None

    @pytest.mark.asyncio
    async def test_init_and_stop(self):
        """Test full init and stop cycle with mocked session."""
        config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="123456:ABC-DEF",
            telegram_chat_id="987654321",
        )

        with patch("aiohttp.ClientSession") as mock_session:
            mock_instance = MagicMock()
            mock_instance.close = AsyncMock()
            mock_session.return_value = mock_instance

            # Mock getMe response
            mock_response = AsyncMock()
            mock_response.json = AsyncMock(
                return_value={"ok": True, "result": {"username": "testbot"}}
            )
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()
            mock_instance.post = MagicMock(return_value=mock_response)

            client = await init_telegram_client(config)
            assert client is not None
            assert get_telegram_client() is client

            await stop_telegram_client()
            assert get_telegram_client() is None


class TestSubscriberTracking:
    """Tests for SSE subscriber tracking used by Telegram integration."""

    def test_has_active_ui_stream_subscribers_empty(self):
        """Test when no subscribers are connected."""
        from app.backend.ui_routes import (
            _ui_stream_subscribers,
            has_active_ui_stream_subscribers,
        )

        # Clear subscribers for test
        original = _ui_stream_subscribers.copy()
        _ui_stream_subscribers.clear()

        try:
            assert has_active_ui_stream_subscribers() is False
        finally:
            _ui_stream_subscribers.update(original)

    def test_has_active_ui_stream_subscribers_with_subscriber(self):
        """Test when subscribers are connected."""
        from app.backend.ui_routes import (
            _ui_stream_subscribers,
            has_active_ui_stream_subscribers,
        )

        # Add a mock subscriber
        original = _ui_stream_subscribers.copy()
        _ui_stream_subscribers.clear()
        _ui_stream_subscribers[asyncio.Queue()] = None

        try:
            assert has_active_ui_stream_subscribers() is True
        finally:
            _ui_stream_subscribers.clear()
            _ui_stream_subscribers.update(original)

    def test_get_subscriber_counts(self):
        """Test getting subscriber counts."""
        from app.backend.ui_routes import get_subscriber_counts

        counts = get_subscriber_counts()
        assert "stream" in counts
        assert "web" in counts
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
        """Test status endpoint (deleted - should return 404)."""
        response = client.get("/api/ui/telegram/status")
        assert response.status_code == 404

    def test_telegram_test_disabled(self, client):
        """Test test endpoint (deleted - should return 404)."""
        response = client.post("/api/ui/telegram/test")
        assert response.status_code == 404


class TestTelegramPolling:
    """Tests for Telegram polling functionality."""

    @pytest.fixture
    def config_with_polling(self):
        """Create test Telegram config with polling enabled."""
        return TelegramConfig(
            bot_token="123456:ABC-DEF",
            chat_id="987654321",
            api_timeout=10,
            max_retries=3,
            retry_delay=0.1,
            max_message_length=4096,
            rate_limit_per_minute=30,
            polling_enabled=True,
            polling_interval=0.1,
            polling_timeout=1,
            allowed_users={"987654321", "111222333"},
        )

    @pytest.fixture
    def client_with_polling(self, config_with_polling):
        """Create TelegramClient with polling enabled."""
        return TelegramClient(config_with_polling)

    def test_config_allowed_users(self):
        """Test that allowed_users is properly populated from config."""
        chat_config = ChatServerConfig(
            telegram_enabled=True,
            telegram_bot_token="123456:ABC-DEF",
            telegram_chat_id="987654321",
            telegram_allowed_users="111222333,444555666",
        )

        tg_config = TelegramConfig.from_chat_config(chat_config)

        # Should include primary chat_id plus allowed_users
        assert "987654321" in tg_config.allowed_users
        assert "111222333" in tg_config.allowed_users
        assert "444555666" in tg_config.allowed_users

    def test_set_message_handler(self, client_with_polling):
        """Test setting a message handler."""

        async def handler(chat_id, text, metadata):
            return f"Echo: {text}"

        assert client_with_polling._message_handler is None
        client_with_polling.set_message_handler(handler)
        assert client_with_polling._message_handler is handler

    @pytest.mark.asyncio
    async def test_start_polling_not_started(self, client_with_polling):
        """Test that polling doesn't start if client not started."""
        await client_with_polling.start_polling()
        assert client_with_polling._polling_task is None

    @pytest.mark.asyncio
    async def test_polling_disabled(self, config_with_polling):
        """Test that polling respects disabled config."""
        config_with_polling.polling_enabled = False
        client = TelegramClient(config_with_polling)

        # Mock session to allow start
        with patch("aiohttp.ClientSession") as mock_session:
            mock_instance = MagicMock()
            mock_instance.close = AsyncMock()  # Make close awaitable
            mock_session.return_value = mock_instance

            # Mock getMe response
            mock_response = AsyncMock()
            mock_response.json = AsyncMock(
                return_value={"ok": True, "result": {"username": "testbot"}}
            )
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()
            mock_instance.post = MagicMock(return_value=mock_response)

            await client.start()
            await client.start_polling()

            assert client._polling_task is None
            await client.stop()

    @pytest.mark.asyncio
    async def test_process_update_unauthorized_chat(self, client_with_polling):
        """Test that messages from unauthorized chats are ignored."""
        client_with_polling._started = True

        # Create update from unauthorized chat
        update = {
            "update_id": 123,
            "message": {
                "message_id": 1,
                "chat": {"id": 999999999, "type": "private"},  # Not in allowed_users
                "from": {"id": 999999999, "username": "hacker"},
                "text": "Hello",
            },
        }

        handler_called = False

        async def handler(chat_id, text, metadata):
            nonlocal handler_called
            handler_called = True
            return "Response"

        client_with_polling.set_message_handler(handler)
        await client_with_polling._process_update(update)

        # Handler should not have been called
        assert handler_called is False

    @pytest.mark.asyncio
    async def test_process_update_authorized_chat(self, client_with_polling):
        """Test that messages from authorized chats are processed."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        # Create update from authorized chat
        update = {
            "update_id": 123,
            "message": {
                "message_id": 1,
                "chat": {"id": 987654321, "type": "private"},  # In allowed_users
                "from": {"id": 987654321, "username": "owner", "first_name": "Owner"},
                "text": "Hello",
            },
        }

        handler_called = False
        received_text = None

        async def handler(chat_id, text, metadata):
            nonlocal handler_called, received_text
            handler_called = True
            received_text = text
            return "Response"

        client_with_polling.set_message_handler(handler)

        # Mock API calls (chat action and send message)
        with (
            patch.object(
                client_with_polling, "_api_call", new=AsyncMock(return_value={"ok": True})
            ),
            patch.object(
                client_with_polling, "send_message_to_chat", new=AsyncMock(return_value=True)
            ),
        ):
            await client_with_polling._process_update(update)

        assert handler_called is True
        assert received_text == "Hello"

    @pytest.mark.asyncio
    async def test_handle_command_start(self, client_with_polling):
        """Test /start command handling."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        sent_messages = []

        async def mock_send(chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return True

        with patch.object(client_with_polling, "send_message_to_chat", side_effect=mock_send):
            await client_with_polling._handle_command("987654321", "/start", {})

        assert len(sent_messages) == 1
        assert "Welcome" in sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_handle_command_help(self, client_with_polling):
        """Test /help command handling."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        sent_messages = []

        async def mock_send(chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return True

        with patch.object(client_with_polling, "send_message_to_chat", side_effect=mock_send):
            await client_with_polling._handle_command("987654321", "/help", {})

        assert len(sent_messages) == 1
        assert "Help" in sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_handle_command_status(self, client_with_polling):
        """Test /status command handling."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        sent_messages = []

        async def mock_send(chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return True

        with patch.object(client_with_polling, "send_message_to_chat", side_effect=mock_send):
            await client_with_polling._handle_command("987654321", "/status", {})

        assert len(sent_messages) == 1
        assert "Status" in sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_handle_command_ping(self, client_with_polling):
        """Test /ping command handling."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        sent_messages = []

        async def mock_send(chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return True

        with patch.object(client_with_polling, "send_message_to_chat", side_effect=mock_send):
            await client_with_polling._handle_command("987654321", "/ping", {})

        assert len(sent_messages) == 1
        assert "Pong" in sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_handle_command_unknown(self, client_with_polling):
        """Test unknown command handling."""
        client_with_polling._started = True
        client_with_polling._session = MagicMock()

        sent_messages = []

        async def mock_send(chat_id, text, **kwargs):
            sent_messages.append((chat_id, text))
            return True

        with patch.object(client_with_polling, "send_message_to_chat", side_effect=mock_send):
            await client_with_polling._handle_command("987654321", "/unknown", {})

        assert len(sent_messages) == 1
        assert "Unknown command" in sent_messages[0][1]

    @pytest.mark.asyncio
    async def test_get_updates(self, client_with_polling):
        """Test getting updates from Telegram API."""
        client_with_polling._started = True

        mock_response = AsyncMock()
        mock_response.json = AsyncMock(
            return_value={
                "ok": True,
                "result": [
                    {"update_id": 100, "message": {"text": "Hello"}},
                    {"update_id": 101, "message": {"text": "World"}},
                ],
            }
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        client_with_polling._session = mock_session

        updates = await client_with_polling._get_updates()

        assert len(updates) == 2
        assert client_with_polling._last_update_id == 101

    @pytest.mark.asyncio
    async def test_send_message_to_chat(self, client_with_polling):
        """Test sending message to a specific chat."""
        client_with_polling._started = True

        mock_response = AsyncMock()
        mock_response.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 1}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_response)
        client_with_polling._session = mock_session

        result = await client_with_polling.send_message_to_chat("111222333", "Test message")

        assert result is True
        # Verify the API was called with correct chat_id
        call_args = mock_session.post.call_args
        assert (
            "111222333" in str(call_args)
            or call_args[1].get("json", {}).get("chat_id") == "111222333"
        )

    @pytest.mark.asyncio
    async def test_stop_polling(self, client_with_polling):
        """Test stopping the polling task."""
        client_with_polling._started = True
        client_with_polling._polling_task = asyncio.create_task(asyncio.sleep(10))

        await client_with_polling.stop_polling()

        assert client_with_polling._polling_task is None


class TestTelegramConnectedMessage:
    """Tests for Telegram 'connected' notification."""

    @pytest.mark.asyncio
    async def test_sends_hi(self):
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.send_message.return_value = True
        telegram.config.chat_id = "555"

        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message.return_value = AgentResponse(
            response="👋 Progenitor connected to Telegram.",
            reasoning=None,
            reasoning_tokens=None,
        )

        await backend_main._send_telegram_connected_message(
            telegram, progenitor, InMemoryDocumentStore()
        )

        telegram.send_message.assert_awaited_once()
        sent_text = telegram.send_message.call_args.args[0]
        assert "👋" in sent_text
        assert "progenitor" in sent_text.lower()
        assert progenitor.send_message.await_args.kwargs["metadata"] == {
            "thread_id": "tele_555",
            "source": "telegram",
        }

    @pytest.mark.asyncio
    async def test_existing_session_is_not_greeted_again(self):
        """A restart must not greet a Telegram chat that already exists."""
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.config.chat_id = "555"
        progenitor = AsyncMock()
        progenitor.is_running = True
        store = InMemoryDocumentStore()
        store.save_document(PROGENITOR_STATE_PREFIX, "session:tele_555", {"title": "Telegram"})

        await backend_main._send_telegram_connected_message(telegram, progenitor, store)

        progenitor.send_message.assert_not_awaited()
        telegram.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_exception_on_send_failure(self):
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.send_message.return_value = False

        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message.return_value = AgentResponse(
            response="👋 Progenitor connected to Telegram.",
            reasoning=None,
            reasoning_tokens=None,
        )

        await backend_main._send_telegram_connected_message(
            telegram, progenitor, InMemoryDocumentStore()
        )

    @pytest.mark.asyncio
    async def test_sends_failure_message_if_agent_greeting_fails(self):
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.send_message.return_value = True

        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message.side_effect = RuntimeError("SSE down")

        await backend_main._send_telegram_connected_message(
            telegram, progenitor, InMemoryDocumentStore()
        )

        telegram.send_message.assert_awaited_once()
        sent_text = telegram.send_message.call_args.args[0]
        assert "failed" in sent_text.lower()


class TestTelegramBackendStreaming:
    """Tests for backend Telegram streaming wiring."""

    @pytest.mark.asyncio
    async def test_handle_telegram_agent_message_streams_to_telegram(self):
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.stream_agent_response_to_chat.return_value = len("Hello world")

        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message_stream_events = MagicMock(
            return_value=_async_event_iter(
                {"type": "stream_chunk", "content": "Hello"},
                {"type": "stream_event", "event_type": "node_update", "content": ""},
                {"type": "stream_chunk", "content": " world"},
            )
        )

        result = await backend_main._handle_telegram_agent_message(
            telegram,
            progenitor,
            "987654321",
            "Hi",
            {"username": "owner", "message_thread_id": 123},
        )

        assert result is None
        telegram.stream_agent_response_to_chat.assert_awaited_once()
        assert telegram.stream_agent_response_to_chat.await_args.kwargs["message_thread_id"] == 123
        assert progenitor.send_message_stream_events.call_args.kwargs["metadata"] == {
            "thread_id": "tele_987654321_123",
            "source": "telegram",
        }

    @pytest.mark.asyncio
    async def test_handle_telegram_agent_message_surfaces_draft_failures(self):
        from app.backend import main as backend_main

        telegram = AsyncMock()
        telegram.stream_agent_response_to_chat.side_effect = TelegramDraftStreamingError(
            "draft unavailable"
        )

        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message_stream_events = MagicMock(
            return_value=_async_event_iter({"type": "stream_chunk", "content": "Hello"})
        )

        result = await backend_main._handle_telegram_agent_message(
            telegram,
            progenitor,
            "987654321",
            "Hi",
            {"username": "owner"},
        )

        assert result is not None
        assert "draft streaming failed" in result.lower()


def _image_config(**overrides) -> TelegramConfig:
    values = {
        "bot_token": "123456:ABC-DEF",
        "chat_id": "987654321",
        "api_timeout": 10,
        "max_retries": 1,
        "retry_delay": 0.01,
        "max_message_length": 4096,
        "rate_limit_per_minute": 30,
        "allowed_users": {"987654321"},
        "max_image_bytes": 1000,
        "media_group_wait": 0.01,
    }
    values.update(overrides)
    return TelegramConfig(**values)


def _photo_sizes(*sizes: tuple[int, int]) -> list[dict]:
    """PhotoSize entries ``(side, file_size)``, smallest first like Telegram sends them."""
    return [
        {
            "file_id": f"photo-{side}",
            "file_unique_id": f"uniq-{side}",
            "width": side,
            "height": side,
            "file_size": file_size,
        }
        for side, file_size in sizes
    ]


def _update(message_id: int = 1, **fields) -> dict:
    message = {
        "message_id": message_id,
        "chat": {"id": 987654321, "type": "private"},
        "from": {"id": 987654321, "username": "owner", "first_name": "Owner"},
        **fields,
    }
    return {"update_id": message_id, "message": message}


class TestTelegramImageExtraction:
    """Which photos/documents in a message become agent images."""

    def test_photo_uses_largest_size_under_the_cap(self):
        client = TelegramClient(_image_config())
        images, errors = client._extract_images(
            {"photo": _photo_sizes((90, 100), (320, 400), (1280, 5000))}
        )
        assert errors == []
        assert [(i.file_id, i.mime_type) for i in images] == [("photo-320", "image/jpeg")]
        assert images[0].filename == "telegram_uniq-320.jpg"

    def test_photo_with_every_size_over_the_cap_is_rejected(self):
        client = TelegramClient(_image_config())
        images, errors = client._extract_images({"photo": _photo_sizes((90, 2000))})
        assert images == []
        assert errors == ["photo is larger than the 0.0 MB limit"]

    def test_image_document_is_accepted(self):
        client = TelegramClient(_image_config())
        images, errors = client._extract_images(
            {
                "document": {
                    "file_id": "doc-1",
                    "file_unique_id": "udoc",
                    "file_name": "diagram.png",
                    "mime_type": "image/png",
                    "file_size": 500,
                }
            }
        )
        assert errors == []
        assert images[0].filename == "telegram_udoc.png"

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            (
                {"file_name": "pic.heic", "mime_type": "image/heic", "file_size": 10},
                "pic.heic: unsupported image type (image/heic)",
            ),
            (
                {"file_name": "notes.pdf", "mime_type": "application/pdf", "file_size": 10},
                "notes.pdf: unsupported file type (application/pdf)",
            ),
            (
                {"file_name": "big.png", "mime_type": "image/png", "file_size": 5 * 1024 * 1024},
                "big.png is 5.0 MB, over the 0.0 MB limit",
            ),
        ],
    )
    def test_unusable_documents_are_reported(self, document, expected):
        client = TelegramClient(_image_config())
        images, errors = client._extract_images({"document": {"file_id": "d", **document}})
        assert images == []
        assert errors == [expected]


class TestTelegramImageUpdates:
    """Inbound photo updates reach the message handler with their images."""

    @staticmethod
    def _client(**overrides) -> tuple[TelegramClient, list[tuple[str, dict]]]:
        client = TelegramClient(_image_config(**overrides))
        client._started = True
        client._session = MagicMock()
        calls: list[tuple[str, dict]] = []

        async def handler(chat_id, text, metadata):
            calls.append((text, metadata))
            return None

        client.set_message_handler(handler)
        return client, calls

    @pytest.mark.asyncio
    async def test_caption_becomes_text_and_images_ride_in_metadata(self):
        client, calls = self._client()
        with patch.object(client, "_api_call", new=AsyncMock(return_value=True)):
            await client._process_update(
                _update(photo=_photo_sizes((320, 400)), caption="/what is this?")
            )
        [(text, metadata)] = calls
        assert text == "/what is this?"  # a caption is never treated as a command
        assert [image.file_id for image in metadata["images"]] == ["photo-320"]
        assert metadata["image_errors"] == []

    @pytest.mark.asyncio
    async def test_photo_without_caption_still_reaches_handler(self):
        client, calls = self._client()
        with patch.object(client, "_api_call", new=AsyncMock(return_value=True)):
            await client._process_update(_update(photo=_photo_sizes((320, 400))))
        [(text, metadata)] = calls
        assert text == ""
        assert len(metadata["images"]) == 1

    @pytest.mark.asyncio
    async def test_unsupported_message_is_ignored(self):
        client, calls = self._client()
        await client._process_update(_update(sticker={"file_id": "s"}))
        assert calls == []

    @pytest.mark.asyncio
    async def test_album_is_collected_into_one_handler_call(self):
        client, calls = self._client()
        with patch.object(client, "_api_call", new=AsyncMock(return_value=True)):
            for index, caption in enumerate(["", "three cats", ""], start=1):
                await client._process_update(
                    _update(
                        message_id=index,
                        media_group_id="album-1",
                        photo=_photo_sizes((100 + index, 400)),
                        caption=caption,
                    )
                )
            assert calls == []  # still waiting for the album to finish
            await asyncio.sleep(0.1)

        [(text, metadata)] = calls
        assert text == "three cats"
        assert [image.file_id for image in metadata["images"]] == [
            "photo-101",
            "photo-102",
            "photo-103",
        ]
        assert metadata["media_group_id"] == "album-1"
        assert client._media_groups == {}

    @pytest.mark.asyncio
    async def test_images_beyond_the_limit_are_dropped_and_reported(self):
        client, calls = self._client(max_images_per_message=2)
        with patch.object(client, "_api_call", new=AsyncMock(return_value=True)):
            for index in range(1, 4):
                await client._process_update(
                    _update(
                        message_id=index,
                        media_group_id="album-2",
                        photo=_photo_sizes((100 + index, 400)),
                    )
                )
            await asyncio.sleep(0.1)

        [(_text, metadata)] = calls
        assert len(metadata["images"]) == 2
        assert metadata["image_errors"] == ["only the first 2 of 3 images were used"]

    @pytest.mark.asyncio
    async def test_stop_polling_drops_pending_albums(self):
        client, calls = self._client(media_group_wait=10)
        await client._process_update(
            _update(media_group_id="album-3", photo=_photo_sizes((100, 400)))
        )
        await client.stop_polling()
        assert client._media_groups == {}
        assert calls == []


class _FakeDownload:
    """Stands in for ``aiohttp.ClientSession.get(...)`` in download tests."""

    def __init__(self, status: int, chunks: list[bytes]) -> None:
        self.status = status
        self.content = MagicMock()

        async def iter_chunked(_size):
            for chunk in chunks:
                yield chunk

        self.content.iter_chunked = iter_chunked

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class TestTelegramDownloadFile:
    @staticmethod
    def _client(get_file_result, download: _FakeDownload | None = None) -> TelegramClient:
        client = TelegramClient(_image_config())
        client._started = True
        client._session = MagicMock()
        client._session.get = MagicMock(return_value=download)
        client._api_call = AsyncMock(return_value=get_file_result)  # type: ignore[method-assign]
        return client

    @pytest.mark.asyncio
    async def test_downloads_file_bytes(self):
        client = self._client(
            {"file_path": "photos/a.jpg", "file_size": 6}, _FakeDownload(200, [b"abc", b"def"])
        )
        assert await client.download_file("f1", max_bytes=100) == b"abcdef"
        url = client._session.get.call_args.args[0]
        assert url == "https://api.telegram.org/file/bot123456:ABC-DEF/photos/a.jpg"

    @pytest.mark.asyncio
    async def test_get_file_failure_is_reported(self):
        client = self._client(None)
        with pytest.raises(TelegramMediaError, match="too large or expired"):
            await client.download_file("f1", max_bytes=100)

    @pytest.mark.asyncio
    async def test_declared_size_over_cap_is_refused_before_download(self):
        client = self._client({"file_path": "a.jpg", "file_size": 5000})
        with pytest.raises(TelegramMediaError, match="over the"):
            await client.download_file("f1", max_bytes=100)
        client._session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_streamed_size_over_cap_is_refused(self):
        client = self._client({"file_path": "a.jpg"}, _FakeDownload(200, [b"x" * 60, b"x" * 60]))
        with pytest.raises(TelegramMediaError, match="over the"):
            await client.download_file("f1", max_bytes=100)

    @pytest.mark.asyncio
    async def test_http_error_does_not_leak_the_token(self):
        client = self._client({"file_path": "a.jpg"}, _FakeDownload(404, []))
        with pytest.raises(TelegramMediaError) as excinfo:
            await client.download_file("f1", max_bytes=100)
        assert "HTTP 404" in str(excinfo.value)
        assert "ABC-DEF" not in str(excinfo.value)


class TestTelegramBackendImages:
    """Backend wiring: images become attachments plus multimodal input."""

    @pytest.fixture(autouse=True)
    def _blob_store(self):
        from app.backend.attachment_blob_store import (
            InMemoryAttachmentBlobStore,
            set_default_attachment_blob_store,
        )
        from app.backend.attachment_routes import get_attachment_manager

        store = InMemoryAttachmentBlobStore()
        set_default_attachment_blob_store(store)
        get_attachment_manager().clear_cache()
        return store

    @staticmethod
    def _progenitor():
        progenitor = AsyncMock()
        progenitor.is_running = True
        progenitor.send_message_stream_events = MagicMock(
            return_value=_async_event_iter({"type": "stream_chunk", "content": "A cat"})
        )
        return progenitor

    @staticmethod
    def _telegram(downloads: dict[str, bytes | Exception]):
        telegram = AsyncMock()
        telegram.config = _image_config()
        telegram.stream_agent_response_to_chat.return_value = len("A cat")

        async def download_file(file_id, max_bytes):
            outcome = downloads[file_id]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        telegram.download_file = AsyncMock(side_effect=download_file)
        return telegram

    @staticmethod
    def _image(file_id: str) -> TelegramImage:
        return TelegramImage(file_id=file_id, file_unique_id=f"u{file_id}", mime_type="image/jpeg")

    @pytest.mark.asyncio
    async def test_images_are_stored_and_sent_as_multimodal_input(self, _blob_store):
        from app.backend import main as backend_main

        telegram = self._telegram({"p1": b"\xff\xd8one", "p2": b"\xff\xd8two"})
        progenitor = self._progenitor()

        result = await backend_main._handle_telegram_agent_message(
            telegram,
            progenitor,
            "987654321",
            "what are these?",
            {
                "chat_id": "987654321",
                "message_thread_id": 7,
                "images": [self._image("p1"), self._image("p2")],
                "image_errors": [],
            },
        )

        assert result is None
        call = progenitor.send_message_stream_events.call_args
        message = call.args[0]
        assert message.startswith("what are these?\n\n<!--progenitor:attachment-context-->")
        assert "telegram_up1.jpg (image/jpeg" in message
        assert call.kwargs["images"] == [
            "data:image/jpeg;base64,/9hvbmU=",
            "data:image/jpeg;base64,/9h0d28=",
        ]
        metadata = call.kwargs["metadata"]
        assert metadata["thread_id"] == "tele_987654321_7"
        assert metadata["source"] == "telegram"
        assert [a["name"] for a in metadata["attachments"]] == [
            "telegram_up1.jpg",
            "telegram_up2.jpg",
        ]
        stored = _blob_store.list_metadata(thread_id="tele_987654321_7")
        assert sorted(meta.filename for meta in stored) == ["telegram_up1.jpg", "telegram_up2.jpg"]
        assert all(meta.text_content is None for meta in stored)
        telegram.send_message_to_chat.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_failure_notifies_user_and_still_sends_the_rest(self):
        from app.backend import main as backend_main

        telegram = self._telegram(
            {"p1": b"\xff\xd8one", "p2": TelegramMediaError("download failed (HTTP 404)")}
        )
        progenitor = self._progenitor()

        await backend_main._handle_telegram_agent_message(
            telegram,
            progenitor,
            "987654321",
            "",
            {"images": [self._image("p1"), self._image("p2")], "image_errors": ["x.heic: nope"]},
        )

        notice = telegram.send_message_to_chat.await_args.args[1]
        assert "x.heic: nope" in notice
        assert "image 2: download failed (HTTP 404)" in notice
        call = progenitor.send_message_stream_events.call_args
        assert len(call.kwargs["images"]) == 1
        message = call.args[0]
        assert message.startswith("<!--progenitor:attachment-context-->")
        assert "image 2: download failed (HTTP 404)" in message.split("Could not be read")[1]

    @pytest.mark.asyncio
    async def test_nothing_usable_skips_the_agent_turn(self):
        from app.backend import main as backend_main

        telegram = self._telegram({"p1": TelegramMediaError("file is over the 5.0 MB limit")})
        progenitor = self._progenitor()

        result = await backend_main._handle_telegram_agent_message(
            telegram, progenitor, "987654321", "", {"images": [self._image("p1")]}
        )

        assert result is None
        telegram.send_message_to_chat.assert_awaited_once()
        progenitor.send_message_stream_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_only_messages_send_no_images(self):
        from app.backend import main as backend_main

        progenitor = self._progenitor()
        await backend_main._handle_telegram_agent_message(
            self._telegram({}), progenitor, "987654321", "hi", {}
        )
        call = progenitor.send_message_stream_events.call_args
        assert call.args[0] == "hi"
        assert call.kwargs["images"] is None
