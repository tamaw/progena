"""Registered channel types. Add a channel type by registering its adapter here."""

from __future__ import annotations

from src.backend.channels.base import ChannelAdapter
from src.backend.channels.telegram.adapter import TelegramChannel

CHANNEL_TYPES: dict[str, type[ChannelAdapter]] = {
    adapter.spec.type: adapter for adapter in (TelegramChannel,)
}
