"""
Telegram Bot API Client for Progenitor Backend.

Sends agent messages to Telegram when no web UI client is connected.
Also supports receiving messages via polling for two-way chat.

Features:
- Async HTTP client using aiohttp
- Exponential backoff retry logic
- Rate limiting
- Message chunking for long messages
- Connection state tracking
- Long polling for incoming messages
- Command handling (/start, /help, /status, /ping)
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Any

import aiohttp

from .config import ChatServerConfig

logger = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    """Telegram-specific configuration extracted from ChatServerConfig."""

    bot_token: str
    chat_id: str
    api_timeout: int
    max_retries: int
    retry_delay: float
    max_message_length: int
    rate_limit_per_minute: int
    draft_update_interval_ms: int = 1000
    # Polling settings
    polling_enabled: bool = True
    polling_interval: float = 2.0
    polling_timeout: int = 30
    allowed_users: set[str] = field(default_factory=set)

    @classmethod
    def from_chat_config(cls, config: ChatServerConfig) -> "TelegramConfig":
        """Extract Telegram config from ChatServerConfig."""
        # Parse allowed users from comma-separated string
        allowed_str = config.telegram_allowed_users.strip()
        allowed_users = set()
        if allowed_str:
            allowed_users = {u.strip() for u in allowed_str.split(",") if u.strip()}
        # Always allow the primary chat_id
        if config.telegram_chat_id:
            allowed_users.add(config.telegram_chat_id)

        return cls(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            api_timeout=config.telegram_api_timeout,
            max_retries=config.telegram_max_retries,
            retry_delay=config.telegram_retry_delay,
            max_message_length=config.telegram_max_message_length,
            rate_limit_per_minute=config.telegram_rate_limit_per_minute,
            draft_update_interval_ms=config.telegram_draft_update_interval_ms,
            polling_enabled=config.telegram_polling_enabled,
            polling_interval=config.telegram_polling_interval,
            polling_timeout=config.telegram_polling_timeout,
            allowed_users=allowed_users,
        )


class TelegramRateLimiter:
    """Simple sliding window rate limiter for Telegram API calls."""

    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Acquire permission to send a message.

        Returns:
            Wait time in seconds (0 if no wait needed).
        """
        async with self._lock:
            now = time.time()
            window_start = now - 60.0

            # Remove timestamps outside the window
            while self._timestamps and self._timestamps[0] < window_start:
                self._timestamps.popleft()

            if len(self._timestamps) < self.max_per_minute:
                # Can send immediately
                self._timestamps.append(now)
                return 0.0

            # Need to wait until oldest timestamp expires
            wait_until = self._timestamps[0] + 60.0
            wait_time = max(0.0, wait_until - now)
            return wait_time


# Type for message handler callback
MessageHandler = Callable[[str, str, dict], Awaitable[str | None]]


class TelegramDraftStreamingError(RuntimeError):
    """Raised when Telegram native draft streaming cannot proceed."""


@dataclass
class TelegramDraftSession:
    """Tracks a native Telegram draft streaming session."""

    chat_id: str
    draft_id: int
    message_thread_id: int | None = None
    segment_text: str = ""
    segment_index: int = 0
    draft_sent: bool = False
    last_draft_text: str = ""
    last_draft_update_at: float | None = None  # None until the first draft update is sent


class TelegramClient:
    """Async Telegram Bot API client with retry logic and rate limiting.

    Supports both sending messages and receiving via polling.
    Only communicates with configured allowed chat IDs.
    """

    BASE_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, config: TelegramConfig):
        """Initialize the Telegram client.

        Args:
            config: Telegram configuration.
        """
        self.config = config
        self._session: aiohttp.ClientSession | None = None
        self._rate_limiter = TelegramRateLimiter(config.rate_limit_per_minute)
        self._started = False
        # Polling state
        self._polling_task: asyncio.Task | None = None
        self._last_update_id: int = 0
        self._message_handler: MessageHandler | None = None
        self._processing_messages: set[int] = set()  # Track messages being processed
        self._draft_id_counter = count(start=max(1, int(time.time() * 1000)))

    def set_message_handler(self, handler: MessageHandler | None) -> None:
        """Set the handler for incoming messages.

        Args:
            handler: Async function (chat_id, message_text, metadata) -> Optional[response_text]
                     If handler returns a string, it will be sent back to the chat.
        """
        self._message_handler = handler

    async def start(self) -> None:
        """Start the client and create HTTP session."""
        if self._started:
            return

        if not self.config.bot_token or not self.config.chat_id:
            logger.warning("Telegram client not configured (missing bot_token or chat_id)")
            return

        timeout = aiohttp.ClientTimeout(total=self.config.api_timeout)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._started = True
        logger.info("Telegram client started")

        # Verify the bot token by calling getMe
        try:
            result = await self._api_call("getMe")
            if result:
                logger.info("Telegram bot verified: @%s", result.get("username", "unknown"))
        except Exception as e:
            logger.warning("Failed to verify Telegram bot: %s", e)

    async def stop(self) -> None:
        """Stop the client, polling, and close HTTP session."""
        # Stop polling first
        await self.stop_polling()

        if self._session:
            await self._session.close()
            self._session = None
        self._started = False
        logger.info("Telegram client stopped")

    # ========================================================================
    # Polling for incoming messages
    # ========================================================================

    async def start_polling(self) -> None:
        """Start the polling task for incoming messages."""
        if not self._started:
            logger.warning("Cannot start polling: client not started")
            return

        if not self.config.polling_enabled:
            logger.info("Telegram polling disabled by configuration")
            return

        if self._polling_task and not self._polling_task.done():
            logger.debug("Polling already running")
            return

        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info("Telegram polling started")

    async def stop_polling(self) -> None:
        """Stop the polling task."""
        if self._polling_task:
            self._polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._polling_task
            self._polling_task = None
            logger.info("Telegram polling stopped")

    async def _polling_loop(self) -> None:
        """Main polling loop that fetches updates from Telegram."""
        logger.info("Starting Telegram polling loop")
        consecutive_errors = 0
        consecutive_timeouts = 0
        consecutive_conflicts = 0
        max_consecutive_errors = 10
        max_consecutive_conflicts = 3  # Stop faster on conflicts

        while True:
            try:
                updates, error_type = await self._get_updates()

                # Handle different error types
                if error_type == "conflict":
                    consecutive_conflicts += 1
                    consecutive_errors += 1
                    if consecutive_conflicts >= max_consecutive_conflicts:
                        logger.error(
                            "Detected %d consecutive 409 Conflict errors - another bot instance "
                            "is likely running. Stopping polling to avoid conflicts.",
                            consecutive_conflicts,
                        )
                        break
                    # Longer backoff for conflicts
                    await asyncio.sleep(min(60, 5 * (2**consecutive_conflicts)))
                    continue
                elif error_type == "timeout":
                    consecutive_timeouts += 1
                    # Timeouts during long polling are normal, only log periodically
                    if consecutive_timeouts == 1 or consecutive_timeouts % 10 == 0:
                        logger.debug(
                            "Long polling timeout (count: %d) - this is normal behavior",
                            consecutive_timeouts,
                        )
                    # Don't count timeouts as errors for the consecutive_errors limit
                    # since they're expected with long polling
                elif error_type == "error":
                    consecutive_errors += 1
                    if consecutive_errors >= max_consecutive_errors:
                        logger.error(
                            "Too many consecutive polling errors (%d), stopping", consecutive_errors
                        )
                        break
                    # Exponential backoff on errors
                    await asyncio.sleep(min(30, 2**consecutive_errors))
                    continue
                else:
                    # Success - reset counters
                    consecutive_errors = 0
                    consecutive_timeouts = 0
                    consecutive_conflicts = 0

                if updates:
                    for update in updates:
                        await self._process_update(update)

                # Small delay between polls (getUpdates already does long polling)
                await asyncio.sleep(self.config.polling_interval)

            except asyncio.CancelledError:
                logger.info("Polling loop cancelled")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    "Unexpected error in polling loop (attempt %d/%d): %s",
                    consecutive_errors,
                    max_consecutive_errors,
                    e,
                )

                if consecutive_errors >= max_consecutive_errors:
                    logger.error("Too many consecutive polling errors, stopping")
                    break

                # Exponential backoff on errors
                await asyncio.sleep(min(30, 2**consecutive_errors))

    async def _get_updates(self) -> tuple[list[dict], str | None]:
        """Fetch updates from Telegram using long polling.

        Returns:
            Tuple of (list of update objects, error_type).
            error_type is None on success, or one of: 'timeout', 'conflict', 'error'
        """
        params = {
            "timeout": self.config.polling_timeout,
            "allowed_updates": ["message"],  # Only get message updates
        }

        if self._last_update_id > 0:
            params["offset"] = self._last_update_id + 1

        # Use longer timeout for the HTTP request during long polling
        # No retry on getUpdates - it's expected to timeout sometimes
        result, error_type = await self._api_call_with_error_type("getUpdates", params, retry=False)

        if error_type:
            return [], error_type

        if not isinstance(result, list):
            return [], None

        # Update the offset to acknowledge received updates
        if result:
            for update in result:
                update_id = update.get("update_id", 0)
                if update_id > self._last_update_id:
                    self._last_update_id = update_id

        return result, None

    async def _process_update(self, update: dict) -> None:
        """Process a single Telegram update.

        Args:
            update: The update object from Telegram.
        """
        message = update.get("message")
        if not message:
            return

        # Extract message details
        message_id = message.get("message_id", 0)
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "private")
        message_thread_id = message.get("message_thread_id")

        from_user = message.get("from", {})
        user_id = str(from_user.get("id", ""))
        username = from_user.get("username", "")
        first_name = from_user.get("first_name", "")

        text = message.get("text", "")

        # Skip if already processing this message
        if message_id in self._processing_messages:
            return

        # Security: Only process messages from allowed users
        if chat_id not in self.config.allowed_users:
            logger.warning(
                "Ignoring message from unauthorized chat: %s (user: %s @%s)",
                chat_id,
                first_name,
                username,
            )
            return

        if not text:
            logger.debug("Ignoring non-text message from %s", chat_id)
            return

        logger.info(
            "Received message from %s (@%s): %s",
            first_name or user_id,
            username or "no_username",
            text[:50] + "..." if len(text) > 50 else text,
        )

        # Handle commands
        if text.startswith("/"):
            await self._handle_command(chat_id, text, message)
            return

        # Process regular message with handler
        if self._message_handler:
            self._processing_messages.add(message_id)
            typing_task: asyncio.Task | None = None
            try:
                # Start a background task to keep typing indicator active
                typing_task = asyncio.create_task(
                    self._keep_typing(chat_id, message_thread_id=message_thread_id)
                )

                metadata = {
                    "chat_id": chat_id,
                    "chat_type": chat_type,
                    "user_id": user_id,
                    "username": username,
                    "first_name": first_name,
                    "message_id": message_id,
                    "message_thread_id": message_thread_id,
                }

                response = await self._message_handler(chat_id, text, metadata)

                if response:
                    await self.send_message_to_chat(
                        chat_id,
                        response,
                        message_thread_id=message_thread_id,
                    )

            except Exception as e:
                logger.error("Error handling message: %s", e)
                await self.send_message_to_chat(
                    chat_id,
                    "❌ Sorry, an error occurred while processing your message.",
                    message_thread_id=message_thread_id,
                )
            finally:
                # Stop the typing indicator task
                if typing_task:
                    typing_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await typing_task
                self._processing_messages.discard(message_id)
        else:
            logger.warning("No message handler configured, ignoring message")

    async def _handle_command(self, chat_id: str, text: str, _message: dict) -> None:
        """Handle bot commands.

        Args:
            chat_id: The chat ID.
            text: The command text (e.g., "/start").
            message: Full message object.
        """
        command = text.split()[0].lower()
        message_thread_id = _message.get("message_thread_id")

        if command == "/start":
            await self.send_message_to_chat(
                chat_id,
                "👋 *Welcome to Progenitor!*\n\n"
                "I'm your AI assistant. Send me any message and I'll respond.\n\n"
                "*Commands:*\n"
                "/start - Show this welcome message\n"
                "/help - Show help information\n"
                "/status - Check connection status\n"
                "/ping - Test responsiveness",
                parse_mode="Markdown",
                message_thread_id=message_thread_id,
            )
        elif command == "/help":
            await self.send_message_to_chat(
                chat_id,
                "🤖 *Progenitor Help*\n\n"
                "Just send me any message and I'll process it through the "
                "Progenitor AI agent.\n\n"
                "*Tips:*\n"
                "• Be specific in your requests\n"
                "• I can handle multi-turn conversations\n"
                "• Long responses may be split into multiple messages\n\n"
                "*Commands:*\n"
                "/status - Check if the agent is connected\n"
                "/ping - Quick connectivity test",
                parse_mode="Markdown",
                message_thread_id=message_thread_id,
            )
        elif command == "/status":
            status = "✅ Connected" if self._message_handler else "⚠️ No handler configured"
            await self.send_message_to_chat(
                chat_id,
                f"📊 *Status*\n\n"
                f"Bot: ✅ Running\n"
                f"Agent: {status}\n"
                f"Polling: {'✅ Active' if self._polling_task else '❌ Inactive'}",
                parse_mode="Markdown",
                message_thread_id=message_thread_id,
            )
        elif command == "/ping":
            await self.send_message_to_chat(
                chat_id,
                "🏓 Pong!",
                message_thread_id=message_thread_id,
            )
        else:
            await self.send_message_to_chat(
                chat_id,
                f"Unknown command: {command}\nUse /help to see available commands.",
                message_thread_id=message_thread_id,
            )

    async def _send_chat_action(
        self,
        chat_id: str,
        action: str = "typing",
        message_thread_id: int | None = None,
    ) -> None:
        """Send a chat action (e.g., typing indicator).

        Args:
            chat_id: The chat ID.
            action: The action ('typing', 'upload_photo', etc.)
        """
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "action": action,
        }
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        await self._api_call("sendChatAction", params, retry=False)

    async def _keep_typing(
        self,
        chat_id: str,
        interval: float = 4.0,
        message_thread_id: int | None = None,
    ) -> None:
        """Keep sending typing indicator while processing.

        Telegram's typing indicator expires after ~5 seconds, so we refresh
        it periodically until the task is cancelled.

        Args:
            chat_id: The chat ID to show typing in.
            interval: Seconds between typing indicator refreshes (default 4s).
        """
        try:
            while True:
                await self._send_chat_action(
                    chat_id,
                    "typing",
                    message_thread_id=message_thread_id,
                )
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # Normal cancellation when processing completes
            pass
        except Exception as e:
            # Don't let typing indicator errors affect message processing
            logger.debug("Error in typing indicator: %s", e)

    async def send_message_to_chat(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        """Send a message to a specific chat.

        Args:
            chat_id: The chat ID to send to.
            text: Message text.
            parse_mode: Optional parse mode.
            disable_notification: Send silently.

        Returns:
            True if successful.
        """
        if not self._started or not self._session:
            logger.debug("Telegram client not started, skipping message")
            return False

        if not text.strip():
            return True

        # Split long messages
        chunks = self._split_message(text)
        success = True

        for i, chunk in enumerate(chunks):
            if not await self._send_message_chunk_to_chat(
                chat_id,
                chunk,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                message_thread_id=message_thread_id,
                reply_to_message_id=reply_to_message_id,
            ):
                logger.warning(
                    "Failed to send message chunk %d/%d to %s", i + 1, len(chunks), chat_id
                )
                success = False

        return success

    def _next_draft_id(self) -> int:
        """Return a unique non-zero draft ID for Telegram draft streaming."""
        return next(self._draft_id_counter)

    def _stream_message_header(self, segment_index: int) -> str:
        """Return the header for a streamed Telegram agent response."""
        return "🤖 Progenitor Agent:\n\n" if segment_index == 0 else ""

    def _stream_segment_limit(self, segment_index: int) -> int:
        """Return the max raw content length for a streamed segment."""
        limit = self.config.max_message_length - len(self._stream_message_header(segment_index))
        if limit <= 0:
            raise TelegramDraftStreamingError(
                "Telegram max_message_length is too small for streamed agent headers"
            )
        return limit

    def _build_stream_segment(self, session: TelegramDraftSession) -> str:
        """Build the visible Telegram text for the current streamed segment."""
        return f"{self._stream_message_header(session.segment_index)}{session.segment_text}"

    def _split_stream_segment(self, text: str, max_len: int) -> tuple[str, str]:
        """Split streamed content into a Telegram-safe committed segment."""
        if len(text) <= max_len:
            return text, ""

        chunk = text[:max_len]
        newline_pos = chunk.rfind("\n")
        if newline_pos > max_len // 2:
            split_at = newline_pos + 1
        else:
            space_pos = chunk.rfind(" ")
            split_at = space_pos + 1 if space_pos > max_len // 2 else max_len

        committed = text[:split_at].rstrip()
        if not committed:
            split_at = max_len
            committed = text[:split_at]
            remainder = text[split_at:]
        else:
            remainder = text[split_at:]

        return committed, remainder.lstrip()

    def _draft_update_interval_seconds(self) -> float:
        """Return the Telegram draft throttling interval in seconds."""
        return max(0.0, self.config.draft_update_interval_ms / 1000.0)

    def _should_flush_draft(
        self,
        session: TelegramDraftSession,
        visible_text: str,
        now: float,
    ) -> bool:
        """Return whether the current draft should be sent to Telegram now."""
        if visible_text == session.last_draft_text:
            return False
        if session.last_draft_update_at is None:
            return True
        interval = self._draft_update_interval_seconds()
        if interval <= 0:
            return True
        return (now - session.last_draft_update_at) >= interval

    async def _send_message_chunk_to_chat(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        """Send a single Telegram message chunk without further splitting."""
        wait_time = await self._rate_limiter.acquire()
        if wait_time > 0:
            logger.debug("Rate limit: waiting %.1f seconds", wait_time)
            await asyncio.sleep(wait_time)

        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if disable_notification:
            params["disable_notification"] = True
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id

        result = await self._api_call("sendMessage", params)
        return result is not None

    async def _send_message_draft(
        self,
        chat_id: str,
        draft_id: int,
        text: str,
        message_thread_id: int | None = None,
        parse_mode: str | None = None,
    ) -> None:
        """Send or update a Telegram native draft message."""
        if not self._started or not self._session:
            raise TelegramDraftStreamingError("Telegram client not started")

        if not text:
            raise TelegramDraftStreamingError("Cannot send an empty Telegram draft")

        wait_time = await self._rate_limiter.acquire()
        if wait_time > 0:
            logger.debug("Rate limit: waiting %.1f seconds", wait_time)
            await asyncio.sleep(wait_time)

        params: dict[str, Any] = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "text": text,
        }
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id
        if parse_mode:
            params["parse_mode"] = parse_mode

        result = await self._api_call("sendMessageDraft", params)
        if result is None:
            raise TelegramDraftStreamingError("Telegram sendMessageDraft call failed")

    async def stream_agent_response_to_chat(
        self,
        chat_id: str,
        chunks: AsyncIterator[str],
        message_thread_id: int | None = None,
    ) -> int:
        """Stream an agent response to Telegram using native message drafts.

        Telegram drafts are used for progressive updates, then each completed
        segment is committed with sendMessage so it appears in chat history.

        Returns:
            Number of raw response characters streamed to Telegram.
        """
        if not self._started or not self._session:
            raise TelegramDraftStreamingError("Telegram client not started")

        session = TelegramDraftSession(
            chat_id=chat_id,
            draft_id=self._next_draft_id(),
            message_thread_id=message_thread_id,
        )
        streamed_chars = 0

        async for chunk in chunks:
            if not chunk:
                continue

            streamed_chars += len(chunk)
            session.segment_text += chunk

            while session.segment_text:
                segment_limit = self._stream_segment_limit(session.segment_index)
                if len(session.segment_text) <= segment_limit:
                    visible_text = self._build_stream_segment(session)
                    now = time.monotonic()
                    if self._should_flush_draft(session, visible_text, now):
                        await self._send_message_draft(
                            chat_id=session.chat_id,
                            draft_id=session.draft_id,
                            text=visible_text,
                            message_thread_id=session.message_thread_id,
                        )
                        session.draft_sent = True
                        session.last_draft_text = visible_text
                        session.last_draft_update_at = now
                    break

                committed_segment, remainder = self._split_stream_segment(
                    session.segment_text,
                    segment_limit,
                )
                session.segment_text = committed_segment
                visible_text = self._build_stream_segment(session)
                if not await self._send_message_chunk_to_chat(
                    chat_id=session.chat_id,
                    text=visible_text,
                    message_thread_id=session.message_thread_id,
                ):
                    raise TelegramDraftStreamingError(
                        "Failed to commit a streamed Telegram message segment"
                    )

                session.segment_index += 1
                session.draft_id = self._next_draft_id()
                session.segment_text = remainder
                session.draft_sent = False
                session.last_draft_text = ""

        if streamed_chars == 0:
            return 0

        if not session.segment_text:
            return streamed_chars

        final_text = self._build_stream_segment(session)
        if not await self._send_message_chunk_to_chat(
            chat_id=session.chat_id,
            text=final_text,
            message_thread_id=session.message_thread_id,
        ):
            raise TelegramDraftStreamingError(
                "Failed to commit the final streamed Telegram message"
            )

        return streamed_chars

    async def _api_call_with_error_type(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> tuple[Any | None, str | None]:
        """Make a Telegram Bot API call with retry logic and error type reporting.

        Args:
            method: API method name (e.g., 'sendMessage', 'getMe').
            params: Optional parameters for the API call.
            retry: Whether to retry on failure.

        Returns:
            Tuple of (API response result, error_type).
            error_type is None on success, or one of: 'timeout', 'conflict', 'error'
        """
        if not self._session:
            logger.warning("Telegram session not initialized for %s call", method)
            return None, "error"

        if params is None:
            params = {}

        url = self.BASE_URL.format(token=self.config.bot_token, method=method)

        last_error = None
        last_error_type: str | None = "error"
        attempts = self.config.max_retries if retry else 1

        for attempt in range(attempts):
            try:
                # Use longer timeout for getUpdates (long polling)
                timeout = None
                if method == "getUpdates":
                    # HTTP timeout should be longer than the Telegram polling timeout
                    polling_timeout = params.get("timeout", 30)
                    timeout = aiohttp.ClientTimeout(total=polling_timeout + 10)

                async with self._session.post(url, json=params, timeout=timeout) as response:
                    data = await response.json()

                    if data.get("ok"):
                        return data.get("result"), None

                    # Handle Telegram API errors
                    error_code = data.get("error_code", 0)
                    description = data.get("description", "Unknown error")

                    # 409 Conflict - another bot instance is running
                    if error_code == 409:
                        logger.warning(
                            "Telegram API conflict (409) on %s: %s - "
                            "ensure only one bot instance is running",
                            method,
                            description,
                        )
                        return None, "conflict"

                    # Rate limit error (429) - wait and retry
                    if error_code == 429:
                        retry_after = data.get("parameters", {}).get("retry_after", 60)
                        logger.warning(
                            "Telegram rate limited on %s, waiting %d seconds", method, retry_after
                        )
                        await asyncio.sleep(retry_after)
                        last_error_type = "error"
                        continue

                    # Bad request (400) - usually invalid parameters, don't retry
                    if error_code == 400:
                        logger.error("Telegram API bad request on %s: %s", method, description)
                        return None, "error"

                    # Unauthorized (401) - invalid token, don't retry
                    if error_code == 401:
                        logger.error("Telegram API unauthorized on %s - check bot token", method)
                        return None, "error"

                    # Forbidden (403) - bot blocked or chat not found, don't retry
                    if error_code == 403:
                        logger.error("Telegram API forbidden on %s: %s", method, description)
                        return None, "error"

                    # Other errors - may retry
                    last_error = f"API error {error_code}: {description}"
                    last_error_type = "error"

            except TimeoutError:
                last_error = "Request timeout"
                last_error_type = "timeout"
                # Don't log timeout as error for getUpdates (it's expected)
                if method != "getUpdates":
                    logger.debug("Telegram API timeout on %s", method)
            except aiohttp.ClientError as e:
                last_error = f"HTTP error: {e}"
                last_error_type = "error"
                logger.debug("Telegram API HTTP error on %s: %s", method, e)
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                last_error_type = "error"
                logger.warning("Telegram API unexpected error on %s: %s", method, e)

            # Exponential backoff before retry
            if attempt < attempts - 1:
                delay = self.config.retry_delay * (2**attempt)
                logger.debug(
                    "Telegram %s call failed (attempt %d/%d): %s, retrying in %.1fs",
                    method,
                    attempt + 1,
                    attempts,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)

        # Only log as error if it's not a timeout on getUpdates
        if method == "getUpdates" and last_error_type == "timeout":
            logger.debug(
                "Telegram %s timed out after %d attempts (normal for long polling)",
                method,
                attempts,
            )
        else:
            logger.error(
                "Telegram %s call failed after %d attempts: %s", method, attempts, last_error
            )
        return None, last_error_type

    async def _api_call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        retry: bool = True,
    ) -> Any | None:
        """Make a Telegram Bot API call with retry logic.

        Args:
            method: API method name (e.g., 'sendMessage', 'getMe').
            params: Optional parameters for the API call.
            retry: Whether to retry on failure.

        Returns:
            API response result on success, None on failure.
        """
        result, _ = await self._api_call_with_error_type(method, params, retry)
        return result

    def _split_message(self, text: str) -> list[str]:
        """Split a long message into chunks that fit Telegram's limit.

        Tries to split at newlines or spaces for readability.

        Args:
            text: The message text to split.

        Returns:
            List of message chunks.
        """
        max_len = self.config.max_message_length
        if len(text) <= max_len:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= max_len:
                chunks.append(remaining)
                break

            # Try to find a good split point
            chunk = remaining[:max_len]

            # Look for newline to split at
            newline_pos = chunk.rfind("\n")
            if newline_pos > max_len // 2:
                split_at = newline_pos + 1
            else:
                # Look for space to split at
                space_pos = chunk.rfind(" ")
                split_at = space_pos + 1 if space_pos > max_len // 2 else max_len

            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        return chunks

    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        disable_notification: bool = False,
    ) -> bool:
        """Send a text message to the configured chat.

        Handles message chunking for long messages and rate limiting.

        Args:
            text: Message text to send.
            parse_mode: Optional parse mode ('HTML', 'Markdown', 'MarkdownV2').
            disable_notification: Send silently (no notification sound).

        Returns:
            True if all message chunks were sent successfully.
        """
        return await self.send_message_to_chat(
            self.config.chat_id,
            text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
        )

    async def send_agent_response(self, content: str) -> bool:
        """Send an agent response to Telegram.

        This is the main method called when forwarding IPC stream content.
        Formats the message appropriately for Telegram.

        Args:
            content: The agent response content.

        Returns:
            True if sent successfully.
        """
        if not content.strip():
            return True

        # Format the message with a header
        # Using plain text to avoid parse issues with special characters
        message = f"🤖 Progenitor Agent:\n\n{content}"

        return await self.send_message(message)


# Global client instance
_telegram_client: TelegramClient | None = None


def get_telegram_client() -> TelegramClient | None:
    """Get the global Telegram client instance."""
    return _telegram_client


async def init_telegram_client(config: ChatServerConfig) -> TelegramClient | None:
    """Initialize the global Telegram client.

    Args:
        config: Chat server configuration.

    Returns:
        The initialized client, or None if Telegram is disabled.
    """
    global _telegram_client

    if not config.telegram_enabled:
        logger.info("Telegram integration disabled")
        return None

    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.warning(
            "Telegram enabled but missing configuration (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)"
        )
        return None

    telegram_config = TelegramConfig.from_chat_config(config)
    _telegram_client = TelegramClient(telegram_config)
    await _telegram_client.start()

    return _telegram_client


async def stop_telegram_client() -> None:
    """Stop the global Telegram client."""
    global _telegram_client

    if _telegram_client:
        await _telegram_client.stop()
        _telegram_client = None
