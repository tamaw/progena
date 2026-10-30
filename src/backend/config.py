"""
Backend Configuration.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from src.backend.env_utils import env_bool, env_int

# Load .env file from project root
load_dotenv()


@dataclass
class ChatServerConfig:
    """Configuration for the backend."""

    # Server settings
    host: str = "0.0.0.0"  # nosec B104 - container/server default must listen on all interfaces
    port: int = 8000
    debug: bool = False

    # Progena settings (legacy subprocess mode)
    progena_script: Path = field(
        default_factory=lambda: Path(__file__).parent.parent / "progena" / "main.py"
    )
    progena_timeout: int = 300  # seconds for request timeout
    progena_stop_timeout: int = 5  # seconds to wait for graceful shutdown (CR-001)
    progena_python: str = field(
        default_factory=lambda: sys.executable
    )  # Use same Python as backend

    # Progena runtime HTTP settings (chat + events over HTTP/SSE)
    progena_runtime_url: str = "http://127.0.0.1:9501"  # Runtime HTTP base URL
    database_url: str = ""  # Postgres URL for the Progena runtime database (read)
    # Backend-owned data lives in a separate database on the *same* Postgres
    # server. Only the database name differs from ``database_url``.
    backend_database_name: str = "backend"

    # API settings
    api_title: str = "Progena AI Chat API"
    api_version: str = "0.1.0"
    model_name: str = "progena-v1"

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
    reasoning_enabled: bool = True  # Include reasoning in response objects by default

    @property
    def backend_database_url(self) -> str:
        """Backend database URL: ``database_url`` with the db name swapped.

        Same DBMS/host/credentials as the Progena runtime database; only the
        database-name path component differs. Returns "" when no base URL is set.
        """
        if not self.database_url or not self.backend_database_name:
            return ""
        parts = urlsplit(self.database_url)
        return urlunsplit(parts._replace(path=f"/{self.backend_database_name}"))

    @classmethod
    def from_env(cls) -> ChatServerConfig:
        """Load config from environment variables."""
        return cls(
            host=os.getenv("CHAT_SERVER_HOST", "0.0.0.0"),  # nosec B104 - same runtime default as above
            port=env_int("CHAT_SERVER_PORT", 8000),
            debug=env_bool("CHAT_SERVER_DEBUG", False),
            progena_timeout=env_int("CHAT_SERVER_PROGENA_TIMEOUT", 300),
            progena_stop_timeout=env_int("CHAT_SERVER_PROGENA_STOP_TIMEOUT", 5),
            progena_python=os.getenv("CHAT_SERVER_PYTHON", sys.executable),
            progena_runtime_url=os.getenv("PROGENA_RUNTIME_URL", "http://127.0.0.1:9501"),
            database_url=os.getenv("PROGENA_DATABASE_URL", ""),
            backend_database_name=os.getenv("BACKEND_DATABASE_NAME", "backend"),
            # API settings
            api_title=os.getenv("CHAT_SERVER_API_TITLE", "Progena AI Chat API"),
            model_name=os.getenv("CHAT_SERVER_MODEL_NAME", "progena-v1"),
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
            reasoning_enabled=env_bool("PROGENA_REASONING_ENABLED", True),
        )
