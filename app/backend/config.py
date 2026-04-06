"""
Backend Configuration.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .env_utils import env_bool, env_float, env_int

# Load .env file from project root
load_dotenv()


@dataclass
class ChatServerConfig:
    """Configuration for the backend."""

    # Server settings
    host: str = "0.0.0.0"  # nosec B104 - container/server default must listen on all interfaces
    port: int = 8000
    debug: bool = False

    # Progenitor settings (legacy subprocess mode)
    progenitor_script: Path = field(
        default_factory=lambda: Path(__file__).parent.parent / "progenitor" / "main.py"
    )
    progenitor_timeout: int = 300  # seconds for request timeout
    progenitor_stop_timeout: int = 5  # seconds to wait for graceful shutdown (CR-001)
    progenitor_python: str = field(
        default_factory=lambda: sys.executable
    )  # Use same Python as backend

    # Progenitor IPC settings (socket-based communication)
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
    rate_limit_cleanup_interval_seconds: int = 300  # Cleanup stale clients every 5 minutes
    rate_limit_stale_client_timeout_seconds: int = 3600  # Seconds before removing inactive clients

    # Streaming settings
    stream_enabled: bool = True  # Enable real-time token streaming
    stream_chunk_delay_ms: int = 0  # Optional delay between chunks (0 = no delay)

    # Telegram integration settings
    telegram_enabled: bool = False  # Enable Telegram notifications when no web client connected
    telegram_bot_token: str = ""  # Bot token from @BotFather
    telegram_chat_id: str = ""  # Your personal chat ID (get from @userinfobot)
    telegram_api_timeout: int = 30  # API request timeout in seconds
    telegram_max_retries: int = 3  # Max retry attempts for failed API calls
    telegram_retry_delay: float = 1.0  # Base delay between retries (exponential backoff)
    telegram_max_message_length: int = 4096  # Telegram max message length
    telegram_rate_limit_per_minute: int = 30  # Rate limit for outgoing messages
    telegram_draft_update_interval_ms: int = 1000  # Minimum delay between sendMessageDraft updates

    # Telegram polling settings (for receiving messages)
    telegram_polling_enabled: bool = True  # Enable polling for incoming messages
    telegram_polling_interval: float = 2.0  # Polling interval in seconds
    telegram_polling_timeout: int = 30  # Long polling timeout (getUpdates timeout param)
    telegram_allowed_users: str = (
        ""  # Comma-separated chat IDs allowed to send messages (empty = only chat_id)
    )

    @classmethod
    def from_env(cls) -> "ChatServerConfig":
        """Load config from environment variables."""
        return cls(
            host=os.getenv("CHAT_SERVER_HOST", "0.0.0.0"),  # nosec B104 - same runtime default as above
            port=env_int("CHAT_SERVER_PORT", 8000),
            debug=env_bool("CHAT_SERVER_DEBUG", False),
            progenitor_timeout=env_int("CHAT_SERVER_PROGENITOR_TIMEOUT", 300),
            progenitor_stop_timeout=env_int("CHAT_SERVER_PROGENITOR_STOP_TIMEOUT", 5),
            progenitor_python=os.getenv("CHAT_SERVER_PYTHON", sys.executable),
            # IPC settings
            progenitor_ipc_host=os.getenv("CHAT_SERVER_IPC_HOST", "127.0.0.1"),
            progenitor_ipc_port=env_int("CHAT_SERVER_IPC_PORT", 9500),
            # API settings
            api_title=os.getenv("CHAT_SERVER_API_TITLE", "Progenitor Chat API"),
            model_name=os.getenv("CHAT_SERVER_MODEL_NAME", "progenitor-v1"),
            rate_limit_enabled=env_bool("CHAT_SERVER_RATE_LIMIT_ENABLED", True),
            rate_limit_per_minute=env_int("CHAT_SERVER_RATE_LIMIT_PER_MINUTE", 10000),
            rate_limit_per_hour=env_int("CHAT_SERVER_RATE_LIMIT_PER_HOUR", 100000),
            rate_limit_cleanup_interval_seconds=env_int(
                "CHAT_SERVER_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS", 300
            ),
            rate_limit_stale_client_timeout_seconds=env_int(
                "CHAT_SERVER_RATE_LIMIT_STALE_CLIENT_TIMEOUT_SECONDS", 3600
            ),
            stream_enabled=env_bool("CHAT_SERVER_STREAM_ENABLED", True),
            stream_chunk_delay_ms=env_int("CHAT_SERVER_STREAM_CHUNK_DELAY_MS", 0),
            # Telegram settings
            telegram_enabled=env_bool("TELEGRAM_ENABLED", False),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            telegram_api_timeout=env_int("TELEGRAM_API_TIMEOUT", 30),
            telegram_max_retries=env_int("TELEGRAM_MAX_RETRIES", 3),
            telegram_retry_delay=env_float("TELEGRAM_RETRY_DELAY", 1.0),
            telegram_max_message_length=env_int("TELEGRAM_MAX_MESSAGE_LENGTH", 4096),
            telegram_rate_limit_per_minute=env_int("TELEGRAM_RATE_LIMIT_PER_MINUTE", 30),
            telegram_draft_update_interval_ms=env_int("TELEGRAM_DRAFT_UPDATE_INTERVAL_MS", 1000),
            # Telegram polling settings
            telegram_polling_enabled=env_bool("TELEGRAM_POLLING_ENABLED", True),
            telegram_polling_interval=env_float("TELEGRAM_POLLING_INTERVAL", 2.0),
            telegram_polling_timeout=env_int("TELEGRAM_POLLING_TIMEOUT", 30),
            telegram_allowed_users=os.getenv("TELEGRAM_ALLOWED_USERS", ""),
        )
