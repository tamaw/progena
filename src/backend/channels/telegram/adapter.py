"""The Telegram channel adapter: runs one bot from a ``channel:telegram`` document."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import src.backend.channels.telegram.bridge as telegram_bridge
from src.backend.channels.base import (
    AdapterStatus,
    ChannelAdapter,
    ChannelError,
    ChannelHost,
)
from src.backend.channels.telegram.client import TelegramClient, TelegramConfig
from src.backend.channels.telegram.settings import (
    TELEGRAM_BOT_TOKEN_CREDENTIAL,
    TELEGRAM_SPEC,
    TelegramSettings,
    seed_from_environment,
)

logger = logging.getLogger(__name__)

TEST_MESSAGE = "✅ Test message from Progena: the Telegram channel is working."
RUNTIME_UNAVAILABLE_REPLY = "⚠️ The Progena runtime is not available right now. Please try again."


class TelegramChannel(ChannelAdapter):
    spec = TELEGRAM_SPEC

    def __init__(
        self,
        channel_id: str,
        settings: Mapping[str, Any],
        secrets: Mapping[str, str],
        host: ChannelHost,
    ) -> None:
        super().__init__(channel_id, settings, secrets, host)
        self._settings = TelegramSettings.from_mapping(settings)
        self._client: TelegramClient | None = None

    @classmethod
    def seed_from_environment(cls) -> tuple[bool, dict[str, Any], dict[str, Any]]:
        return seed_from_environment()

    @property
    def client(self) -> TelegramClient | None:
        return self._client

    async def start(self) -> None:
        client = TelegramClient(
            TelegramConfig.from_settings(
                self._settings, self.secrets.get(TELEGRAM_BOT_TOKEN_CREDENTIAL, "")
            )
        )
        client.set_message_handler(self._handle_message)
        client.set_state_change_callback(lambda: self.host.status_changed(self.channel_id))
        self._client = client
        await client.start()
        if not client.started:
            return
        await client.start_polling()
        runtime = self.host.runtime_client()
        if runtime is not None and client.last_error is None:
            # Greets once: a chat that already has a session stays quiet.
            self.host.spawn(
                telegram_bridge._send_telegram_connected_message(
                    client, runtime, self.host.document_store()
                )
            )

    async def stop(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.set_state_change_callback(None)
            await client.stop()

    def status(self) -> AdapterStatus:
        client = self._client
        if client is None or not client.started:
            return AdapterStatus("disconnected", "Not running")
        handle = f"@{client.bot_username}" if client.bot_username else None
        if client.last_error:
            return AdapterStatus("error", client.last_error, handle)
        if not self._settings.polling_enabled:
            return AdapterStatus(
                "connected", "Notifications only: receiving messages is off", handle
            )
        if not client.polling_active:
            return AdapterStatus("disconnected", "Polling is not running", handle)
        return AdapterStatus("connected", "Receiving and sending messages", handle)

    async def send_test(self) -> None:
        client = self._client
        if client is None or not client.started:
            raise ChannelError("Telegram is not connected")
        if not await client.send_message(TEST_MESSAGE):
            raise ChannelError(client.last_api_error or "Telegram did not accept the message")
        self.host.record_message(self.channel_id, "outbound")

    async def notify(self, content: str, event_type: str) -> bool:
        client = self._client
        if client is None or not client.started:
            return False
        sent = await client.send_agent_response(content)
        if sent:
            self.host.record_message(self.channel_id, "outbound")
            logger.info("Sent %s to Telegram (%d chars)", event_type, len(content))
        else:
            logger.warning("Failed to send %s to Telegram", event_type)
        return sent

    async def _handle_message(self, chat_id: str, message: str, metadata: dict) -> str | None:
        """Forward one inbound chat message to Progena and stream the reply back."""
        self.host.record_message(self.channel_id, "inbound")
        client = self._client
        runtime = self.host.runtime_client()
        if client is None or runtime is None:
            reply: str | None = RUNTIME_UNAVAILABLE_REPLY
        else:
            reply = await telegram_bridge._handle_telegram_agent_message(
                client, runtime, chat_id, message, metadata
            )
        self.host.record_message(self.channel_id, "outbound")
        return reply
