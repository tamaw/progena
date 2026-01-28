"""
Langfuse Configuration Module.

Handles loading and validation of Langfuse environment variables.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from ..env_utils import env_bool

logger = logging.getLogger(__name__)


@dataclass
class LangfuseConfig:
    """Langfuse configuration settings."""

    enabled: bool
    public_key: Optional[str]
    secret_key: Optional[str]
    host: str
    debug: bool
    sample_rate: float

    @classmethod
    def from_env(cls) -> "LangfuseConfig":
        """Load configuration from environment variables."""
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")

        # Auto-enable if keys are present, unless explicitly disabled
        has_keys = bool(public_key and secret_key)
        enabled = env_bool("LANGFUSE_ENABLED", default=has_keys)

        # Validate: if enabled, keys must be present
        if enabled and not has_keys:
            logger.warning(
                "Langfuse is enabled but LANGFUSE_PUBLIC_KEY and/or LANGFUSE_SECRET_KEY "
                "are not set. Disabling Langfuse integration."
            )
            enabled = False

        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        debug = env_bool("LANGFUSE_DEBUG", default=False)

        # Sample rate for tracing (0.0 to 1.0)
        sample_rate_str = os.getenv("LANGFUSE_SAMPLE_RATE", "1.0")
        try:
            sample_rate = float(sample_rate_str)
            sample_rate = max(0.0, min(1.0, sample_rate))
        except ValueError:
            logger.warning(
                "Invalid LANGFUSE_SAMPLE_RATE: %s, using default 1.0",
                sample_rate_str
            )
            sample_rate = 1.0

        config = cls(
            enabled=enabled,
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            debug=debug,
            sample_rate=sample_rate,
        )

        if config.enabled:
            logger.info(
                "Langfuse observability enabled (host: %s, sample_rate: %.2f)",
                config.host,
                config.sample_rate,
            )
        else:
            logger.info("Langfuse observability disabled")

        return config

    def is_configured(self) -> bool:
        """Check if Langfuse is properly configured and enabled."""
        return self.enabled and bool(self.public_key) and bool(self.secret_key)


# Singleton config instance
_config: Optional[LangfuseConfig] = None


def get_langfuse_config() -> LangfuseConfig:
    """Get the Langfuse configuration singleton."""
    global _config
    if _config is None:
        _config = LangfuseConfig.from_env()
    return _config


def reset_config() -> None:
    """Reset the configuration singleton (for testing)."""
    global _config
    _config = None
