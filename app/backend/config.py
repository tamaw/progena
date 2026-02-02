"""
Backend Configuration.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .env_utils import env_int, env_bool


@dataclass
class ChatServerConfig:
    """Configuration for the backend."""

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Progenitor settings (legacy subprocess mode)
    progenitor_script: Path = field(
        default_factory=lambda: Path(__file__).parent.parent / "progenitor" / "main.py"
    )
    progenitor_timeout: int = 300  # seconds for request timeout
    progenitor_stop_timeout: int = 5  # seconds to wait for graceful shutdown (CR-001)
    progenitor_python: str = field(default_factory=lambda: sys.executable)  # Use same Python as backend

    # Progenitor IPC settings (socket-based communication - recommended)
    progenitor_ipc_enabled: bool = True  # Use socket IPC instead of subprocess
    progenitor_ipc_host: str = "127.0.0.1"  # IPC server host
    progenitor_ipc_port: int = 9500  # IPC server port

    # API settings
    api_title: str = "Progenitor Chat API"
    api_version: str = "0.1.0"
    model_name: str = "progenitor-v1"

    # CORS
    cors_origins: list[str] = field(default_factory=lambda: ["*"])

    # Rate limiting (high limits for personal/dev use)
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 10000
    rate_limit_per_hour: int = 100000
    rate_limit_cleanup_interval: int = 300  # Cleanup stale clients every 5 minutes
    rate_limit_stale_client_timeout_seconds: int = 3600  # Seconds before removing inactive clients

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
            progenitor_stop_timeout=env_int("CHAT_SERVER_PROGENITOR_STOP_TIMEOUT", 5),
            progenitor_python=os.getenv("CHAT_SERVER_PYTHON", sys.executable),
            # IPC settings
            progenitor_ipc_enabled=env_bool("CHAT_SERVER_IPC_ENABLED", True),
            progenitor_ipc_host=os.getenv("CHAT_SERVER_IPC_HOST", "127.0.0.1"),
            progenitor_ipc_port=env_int("CHAT_SERVER_IPC_PORT", 9500),
            # API settings
            api_title=os.getenv("CHAT_SERVER_API_TITLE", "Progenitor Chat API"),
            model_name=os.getenv("CHAT_SERVER_MODEL_NAME", "progenitor-v1"),
            rate_limit_enabled=env_bool("CHAT_SERVER_RATE_LIMIT_ENABLED", True),
            rate_limit_per_minute=env_int("CHAT_SERVER_RATE_LIMIT_PER_MINUTE", 10000),
            rate_limit_per_hour=env_int("CHAT_SERVER_RATE_LIMIT_PER_HOUR", 100000),
            stream_enabled=env_bool("CHAT_SERVER_STREAM_ENABLED", True),
            stream_chunk_delay_ms=env_int("CHAT_SERVER_STREAM_CHUNK_DELAY_MS", 0),
        )
