"""The Telegram channel type: descriptor, settings and environment seed."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.backend.channels.base import (
    ChannelCapabilities,
    ChannelTypeSpec,
    CredentialField,
    SettingField,
)
from src.backend.env_utils import env_bool, env_float, env_int
from src.backend.http.contracts import SOURCE_TELEGRAM
from src.progena.channels.contracts import CredentialSource

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # nosec B105 - environment variable name, not a secret
# Credential key of the bot token in the channel document.
TELEGRAM_BOT_TOKEN_CREDENTIAL = "bot_token"  # nosec B105 - credential name, not a secret

TELEGRAM_SPEC = ChannelTypeSpec(
    type="telegram",
    name="Telegram",
    category="Messaging",
    description=(
        "A Telegram bot for two-way chat with Progena. Messages from allowed chats "
        "start or continue a conversation (one per chat, or per forum topic) and "
        "replies stream back as native Telegram drafts. While no browser is "
        "connected, agent messages, questions and scheduled results are forwarded "
        "to the primary chat."
    ),
    session_source=SOURCE_TELEGRAM,
    capabilities=ChannelCapabilities(
        inbound=True, outbound=True, streaming=True, threads=True, attachments=False
    ),
    fields=(
        SettingField(
            "chat_id",
            "Chat ID",
            "text",
            "",
            help="Your chat with the bot (ask @userinfobot). Receives notifications.",
            required=True,
            placeholder="123456789",
        ),
        SettingField(
            "allowed_chat_ids",
            "Allowed chat IDs",
            "list",
            [],
            help="Other chats or groups allowed to message Progena. The primary chat is always allowed.",
        ),
        SettingField(
            "polling_enabled",
            "Receive messages",
            "boolean",
            True,
            help="Poll Telegram for incoming messages. Off makes the channel notification-only.",
        ),
        SettingField(
            "notify_when_away",
            "Notify when away",
            "boolean",
            True,
            help="Forward agent messages, questions and scheduled results while no browser is open.",
        ),
        SettingField(
            "polling_interval",
            "Polling interval (s)",
            "number",
            2.0,
            help="Pause between long-poll requests.",
            advanced=True,
            minimum=0,
            maximum=60,
            step=0.5,
        ),
        SettingField(
            "polling_timeout",
            "Long-poll timeout (s)",
            "integer",
            30,
            help="How long Telegram holds each getUpdates request open.",
            advanced=True,
            minimum=1,
            maximum=50,
            step=1,
        ),
        SettingField(
            "api_timeout",
            "API timeout (s)",
            "integer",
            30,
            help="Timeout for each Bot API request.",
            advanced=True,
            minimum=1,
            maximum=300,
            step=1,
        ),
        SettingField(
            "max_retries",
            "Max retries",
            "integer",
            3,
            help="Attempts for a failed Bot API request.",
            advanced=True,
            minimum=1,
            maximum=10,
            step=1,
        ),
        SettingField(
            "retry_delay",
            "Retry delay (s)",
            "number",
            1.0,
            help="Base delay between retries (doubles each attempt).",
            advanced=True,
            minimum=0,
            maximum=60,
            step=0.5,
        ),
        SettingField(
            "max_message_length",
            "Max message length",
            "integer",
            4096,
            help="Longer replies are split into several messages. Telegram's limit is 4096.",
            advanced=True,
            minimum=256,
            maximum=4096,
            step=256,
        ),
        SettingField(
            "rate_limit_per_minute",
            "Rate limit (per minute)",
            "integer",
            30,
            help="Outgoing Bot API calls allowed per minute.",
            advanced=True,
            minimum=1,
            maximum=120,
            step=1,
        ),
        SettingField(
            "draft_update_interval_ms",
            "Draft refresh interval (ms)",
            "integer",
            1000,
            help="Minimum time between streamed draft updates.",
            advanced=True,
            minimum=0,
            maximum=10000,
            step=100,
        ),
    ),
    credentials=(
        CredentialField(
            TELEGRAM_BOT_TOKEN_CREDENTIAL,
            "Bot token",
            TELEGRAM_BOT_TOKEN_ENV,
            help="The token @BotFather issued for your bot.",
        ),
    ),
)


@dataclass(frozen=True)
class TelegramSettings:
    """Validated Telegram settings (see :data:`TELEGRAM_SPEC`)."""

    chat_id: str
    allowed_chat_ids: tuple[str, ...]
    polling_enabled: bool
    notify_when_away: bool
    polling_interval: float
    polling_timeout: int
    api_timeout: int
    max_retries: int
    retry_delay: float
    max_message_length: int
    rate_limit_per_minute: int
    draft_update_interval_ms: int

    @classmethod
    def from_mapping(cls, settings: Mapping[str, Any]) -> TelegramSettings:
        values = TELEGRAM_SPEC.validate_settings(settings)
        return cls(**{**values, "allowed_chat_ids": tuple(values["allowed_chat_ids"])})


def seed_from_environment() -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """The first ``channel:telegram`` document from the legacy TELEGRAM_* variables.

    Used once, when no Telegram document exists. Afterwards the document is the
    source of truth and these variables (other than a referenced token) are unused.
    """
    allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    settings = TELEGRAM_SPEC.lenient_settings(
        {
            "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
            "allowed_chat_ids": [item for item in allowed.split(",") if item.strip()],
            "polling_enabled": env_bool("TELEGRAM_POLLING_ENABLED", True),
            "polling_interval": env_float("TELEGRAM_POLLING_INTERVAL", 2.0),
            "polling_timeout": env_int("TELEGRAM_POLLING_TIMEOUT", 30),
            "api_timeout": env_int("TELEGRAM_API_TIMEOUT", 30),
            "max_retries": env_int("TELEGRAM_MAX_RETRIES", 3),
            "retry_delay": env_float("TELEGRAM_RETRY_DELAY", 1.0),
            "max_message_length": env_int("TELEGRAM_MAX_MESSAGE_LENGTH", 4096),
            "rate_limit_per_minute": env_int("TELEGRAM_RATE_LIMIT_PER_MINUTE", 30),
            "draft_update_interval_ms": env_int("TELEGRAM_DRAFT_UPDATE_INTERVAL_MS", 1000),
        }
    )
    credentials = {
        TELEGRAM_BOT_TOKEN_CREDENTIAL: {
            "source": CredentialSource.ENVIRONMENT.value,
            "env_var": TELEGRAM_BOT_TOKEN_ENV,
        }
    }
    return env_bool("TELEGRAM_ENABLED", False), settings, credentials
