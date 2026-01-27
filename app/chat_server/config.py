"""
Chat Server Configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..common.env_utils import env_int, env_bool


@dataclass
class ChatServerConfig:
    """Configuration for the chat server."""

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Progenitor settings
    progenitor_script: Path = field(
        default_factory=lambda: Path(__file__).parent.parent / "progenitor" / "main.py"
    )
    progenitor_timeout: int = 300  # seconds
    progenitor_python: str = "python"

    # API settings
    api_title: str = "Progenitor Chat API"
    api_version: str = "0.1.0"
    model_name: str = "progenitor-v1"

    # CORS
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 60
    rate_limit_per_hour: int = 500

    # Streaming settings
    stream_enabled: bool = True  # Enable real-time token streaming
    stream_chunk_delay_ms: int = 0  # Optional delay between chunks (0 = no delay)

    @classmethod
    def from_env(cls) -> "ChatServerConfig":
        """Load config from environment variables."""
        return cls(
            host=os.getenv("CHAT_SERVER_HOST", "0.0.0.0"),
            port=env_int("CHAT_SERVER_PORT", 8000),
            debug=env_bool("CHAT_SERVER_DEBUG", False),
            progenitor_timeout=env_int("CHAT_SERVER_PROGENITOR_TIMEOUT", 300),
            progenitor_python=os.getenv("CHAT_SERVER_PYTHON", "python"),
            api_title=os.getenv("CHAT_SERVER_API_TITLE", "Progenitor Chat API"),
            model_name=os.getenv("CHAT_SERVER_MODEL_NAME", "progenitor-v1"),
            rate_limit_enabled=env_bool("CHAT_SERVER_RATE_LIMIT_ENABLED", True),
            rate_limit_per_minute=env_int("CHAT_SERVER_RATE_LIMIT_PER_MINUTE", 60),
            rate_limit_per_hour=env_int("CHAT_SERVER_RATE_LIMIT_PER_HOUR", 500),
            stream_enabled=env_bool("CHAT_SERVER_STREAM_ENABLED", True),
            stream_chunk_delay_ms=env_int("CHAT_SERVER_STREAM_CHUNK_DELAY_MS", 0),
        )
