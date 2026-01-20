"""
Chat Server Configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _env_int(key: str, default: int) -> int:
    """Parse integer environment variable."""
    val = os.getenv(key)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            pass
    return default


def _env_bool(key: str, default: bool = False) -> bool:
    """Parse boolean environment variable."""
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


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

    @classmethod
    def from_env(cls) -> "ChatServerConfig":
        """Load config from environment variables."""
        return cls(
            host=os.getenv("CHAT_SERVER_HOST", "0.0.0.0"),
            port=_env_int("CHAT_SERVER_PORT", 8000),
            debug=_env_bool("CHAT_SERVER_DEBUG", False),
            progenitor_timeout=_env_int("CHAT_SERVER_PROGENITOR_TIMEOUT", 300),
            progenitor_python=os.getenv("CHAT_SERVER_PYTHON", "python"),
            api_title=os.getenv("CHAT_SERVER_API_TITLE", "Progenitor Chat API"),
            model_name=os.getenv("CHAT_SERVER_MODEL_NAME", "progenitor-v1"),
        )
