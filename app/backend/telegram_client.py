"""
Telegram Bot API Client for Progenitor Backend.

Sends agent messages to Telegram when no web UI client is connected.
Only forwards IPC stream content (agent responses), ignores stats/status.

Features:
- Async HTTP client using aiohttp
- Exponential backoff retry logic
- Rate limiting
- Message chunking for long messages
- Connection state tracking
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional
from collections import deque

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

    @classmethod
    def from_chat_config(cls, config: ChatServerConfig) -> "TelegramConfig":
        """Extract Telegram config from ChatServerConfig."""
        return cls(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            api_timeout=config.telegram_api_timeout,
            max_retries=config.telegram_max_retries,
            retry_delay=config.telegram_retry_delay,
            max_message_length=config.telegram_max_message_length,
            rate_limit_per_minute=config.telegram_rate_limit_per_minute,
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


class TelegramClient:
    """Async Telegram Bot API client with retry logic and rate limiting.

    Only sends messages to a single configured chat (your personal chat).
    """

    BASE_URL = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, config: TelegramConfig):
        """Initialize the Telegram client.

        Args:
            config: Telegram configuration.
        """
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = TelegramRateLimiter(config.rate_limit_per_minute)
        self._started = False

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
        """Stop the client and close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._started = False
        logger.info("Telegram client stopped")

    async def _api_call(
        self,
        method: str,
        params: Optional[dict] = None,
        retry: bool = True,
    ) -> Optional[dict]:
        """Make a Telegram Bot API call with retry logic.

        Args:
            method: API method name (e.g., 'sendMessage', 'getMe').
            params: Optional parameters for the API call.
            retry: Whether to retry on failure.

        Returns:
            API response result on success, None on failure.
        """
        if not self._session:
            logger.warning("Telegram session not initialized")
            return None

        url = self.BASE_URL.format(token=self.config.bot_token, method=method)

        last_error = None
        attempts = self.config.max_retries if retry else 1

        for attempt in range(attempts):
            try:
                async with self._session.post(url, json=params) as response:
                    data = await response.json()

                    if data.get("ok"):
                        return data.get("result")

                    # Handle Telegram API errors
                    error_code = data.get("error_code", 0)
                    description = data.get("description", "Unknown error")

                    # Rate limit error (429) - wait and retry
                    if error_code == 429:
                        retry_after = data.get("parameters", {}).get("retry_after", 60)
                        logger.warning(
                            "Telegram rate limited, waiting %d seconds",
                            retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    # Bad request (400) - usually invalid parameters, don't retry
                    if error_code == 400:
                        logger.error("Telegram API bad request: %s", description)
                        return None

                    # Unauthorized (401) - invalid token, don't retry
                    if error_code == 401:
                        logger.error("Telegram API unauthorized - check bot token")
                        return None

                    # Forbidden (403) - bot blocked or chat not found, don't retry
                    if error_code == 403:
                        logger.error("Telegram API forbidden: %s", description)
                        return None

                    # Other errors - may retry
                    last_error = f"API error {error_code}: {description}"

            except asyncio.TimeoutError:
                last_error = "Request timeout"
            except aiohttp.ClientError as e:
                last_error = f"HTTP error: {e}"
            except Exception as e:
                last_error = f"Unexpected error: {e}"

            # Exponential backoff before retry
            if attempt < attempts - 1:
                delay = self.config.retry_delay * (2 ** attempt)
                logger.debug(
                    "Telegram API call failed (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1, attempts, last_error, delay
                )
                await asyncio.sleep(delay)

        logger.error(
            "Telegram API call failed after %d attempts: %s",
            attempts, last_error
        )
        return None

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
                if space_pos > max_len // 2:
                    split_at = space_pos + 1
                else:
                    # Hard split at max length
                    split_at = max_len

            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        return chunks

    async def send_message(
        self,
        text: str,
        parse_mode: Optional[str] = None,
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
        if not self._started or not self._session:
            logger.debug("Telegram client not started, skipping message")
            return False

        if not text.strip():
            logger.debug("Empty message, skipping")
            return True

        # Split long messages
        chunks = self._split_message(text)
        success = True

        for i, chunk in enumerate(chunks):
            # Apply rate limiting
            wait_time = await self._rate_limiter.acquire()
            if wait_time > 0:
                logger.debug("Rate limit: waiting %.1f seconds", wait_time)
                await asyncio.sleep(wait_time)

            # Build parameters
            params = {
                "chat_id": self.config.chat_id,
                "text": chunk,
            }
            if parse_mode:
                params["parse_mode"] = parse_mode
            if disable_notification:
                params["disable_notification"] = True

            # Send the message
            result = await self._api_call("sendMessage", params)
            if result is None:
                logger.warning(
                    "Failed to send message chunk %d/%d",
                    i + 1, len(chunks)
                )
                success = False

        return success

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
_telegram_client: Optional[TelegramClient] = None


def get_telegram_client() -> Optional[TelegramClient]:
    """Get the global Telegram client instance."""
    return _telegram_client


async def init_telegram_client(config: ChatServerConfig) -> Optional[TelegramClient]:
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
            "Telegram enabled but missing configuration "
            "(TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)"
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
