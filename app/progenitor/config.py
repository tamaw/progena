"""
Centralized Configuration for Progenitor.

Configuration is read from environment variables (preferred) with JSON config file fallbacks.
Environment variables always win. Per SPEC.md.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from dotenv import load_dotenv

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    """Parse boolean environment variable."""
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


def _env_int(key: str, default: int) -> int:
    """Parse integer environment variable."""
    val = os.getenv(key)
    if val is not None:
        try:
            return int(val)
        except ValueError:
            logger.warning(f"Invalid integer for {key}: {val}, using default {default}")
    return default


def _env_float(key: str, default: float) -> float:
    """Parse float environment variable."""
    val = os.getenv(key)
    if val is not None:
        try:
            return float(val)
        except ValueError:
            logger.warning(f"Invalid float for {key}: {val}, using default {default}")
    return default


def _env_list(key: str, default: list[str] | None = None) -> list[str]:
    """Parse comma-separated list environment variable."""
    val = os.getenv(key)
    if val:
        return [item.strip() for item in val.split(",") if item.strip()]
    return default or []


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: str = "azure"  # "openai" | "azure"

    # OpenAI (public)
    openai_api_key: Optional[str] = None
    model_default: str = "gpt-5.1-codex-max"

    # Azure OpenAI
    azure_endpoint: Optional[str] = None
    azure_api_key: Optional[str] = None
    azure_api_version: str = "2024-08-01-preview"
    azure_deployment_default: Optional[str] = None

    # Per-role deployments (Azure)
    azure_deployment_planner: Optional[str] = None
    azure_deployment_builder: Optional[str] = None
    azure_deployment_summary: Optional[str] = None

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM config from environment."""
        provider = os.getenv("PROGENITOR_LLM_PROVIDER", "").lower()

        # Auto-detect Azure if endpoint is set
        if not provider and os.getenv("AZURE_OPENAI_ENDPOINT"):
            provider = "azure"

        return cls(
            provider=provider or "azure",
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.1-codex-max"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            azure_deployment_default=os.getenv("AZURE_OPENAI_DEPLOYMENT_DEFAULT"),
            azure_deployment_planner=os.getenv("AZURE_OPENAI_DEPLOYMENT_PLANNER"),
            azure_deployment_builder=os.getenv("AZURE_OPENAI_DEPLOYMENT_BUILDER"),
            azure_deployment_summary=os.getenv("AZURE_OPENAI_DEPLOYMENT_SUMMARY"),
        )

    def get_deployment_for_role(self, role: str) -> Optional[str]:
        """Get deployment name for a specific role."""
        role_deployments = {
            "planner": self.azure_deployment_planner,
            "builder": self.azure_deployment_builder,
            "summary": self.azure_deployment_summary,
        }
        return role_deployments.get(role) or self.azure_deployment_default


@dataclass
class RuntimeConfig:
    """Runtime configuration."""

    timezone: str = "Australia/Melbourne"
    idle_backoff_seconds: int = 30
    max_actions_per_cycle: int = 10
    log_level: str = "info"
    backoff_multiplier: float = 1.5
    max_backoff_seconds: int = 300
    checkpoint_interval_seconds: int = 30
    input_timeout_seconds: int = 300

    @classmethod
    def from_env(cls, file_config: dict[str, Any] | None = None) -> "RuntimeConfig":
        """Load runtime config from environment with file fallbacks."""
        fc = file_config or {}
        return cls(
            timezone=os.getenv("PROGENITOR_TIMEZONE", fc.get("timezone", "Australia/Melbourne")),
            idle_backoff_seconds=_env_int(
                "PROGENITOR_IDLE_BACKOFF_SECONDS", fc.get("idle_backoff_seconds", 30)
            ),
            max_actions_per_cycle=_env_int(
                "PROGENITOR_MAX_ACTIONS_PER_CYCLE", fc.get("max_actions_per_cycle", 10)
            ),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", fc.get("log_level", "info")),
            backoff_multiplier=_env_float(
                "PROGENITOR_BACKOFF_MULTIPLIER", fc.get("backoff_multiplier", 1.5)
            ),
            max_backoff_seconds=_env_int(
                "PROGENITOR_MAX_BACKOFF_SECONDS", fc.get("max_backoff_seconds", 300)
            ),
            checkpoint_interval_seconds=_env_int(
                "PROGENITOR_CHECKPOINT_INTERVAL_SECONDS", fc.get("checkpoint_interval_seconds", 30)
            ),
            input_timeout_seconds=_env_int(
                "PROGENITOR_INPUT_TIMEOUT_SECONDS", fc.get("input_timeout_seconds", 300)
            ),
        )


@dataclass
class StorageConfig:
    """Storage paths configuration."""

    state_dir: Path = field(default_factory=lambda: Path("/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    state_lock_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
        """Load storage config from environment."""
        # Default to project-relative paths if not in container
        default_state = "/state" if project_root is None else str(project_root / "state")
        default_logs = "/logs" if project_root is None else str(project_root / "logs")
        default_worktrees = "/worktrees" if project_root is None else str(project_root / "worktrees")

        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", default_state)),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", default_logs)),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", default_worktrees)),
            state_lock_timeout_seconds=_env_int("PROGENITOR_STATE_LOCK_TIMEOUT_SECONDS", 10),
        )


@dataclass
class CopilotConfig:
    """Copilot CLI configuration."""

    bin_path: str = "copilot"
    config_path: Path = field(default_factory=lambda: Path("/config/copilot.json"))
    timeout_seconds: int = 300
    default_flags: list[str] = field(default_factory=lambda: ["--yolo"])
    model: str = "claude-haiku-4.5"
    max_tokens: int = 8192

    @classmethod
    def from_env(cls, file_config: dict[str, Any] | None = None, project_root: Path | None = None) -> "CopilotConfig":
        """Load Copilot config from environment with file fallbacks."""
        fc = file_config or {}
        default_config_path = "/config/copilot.json" if project_root is None else str(project_root / "config" / "copilot.json")

        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", fc.get("command", "copilot")),
            config_path=Path(os.getenv("PROGENITOR_COPILOT_CONFIG", default_config_path)),
            timeout_seconds=_env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", fc.get("timeout", 300)),
            default_flags=fc.get("default_flags", ["--yolo"]),
            model=fc.get("model", "claude-haiku-4.5"),
            max_tokens=fc.get("max_tokens", 8192),
        )


@dataclass
class TransportConfig:
    """Transport/messaging configuration."""

    transport: str = "whatsapp"  # "whatsapp" | "cli" | "webhook"
    inbox_endpoint: Optional[str] = None
    outbox_endpoint: Optional[str] = None
    allowed_senders: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "TransportConfig":
        """Load transport config from environment."""
        return cls(
            transport=os.getenv("PROGENITOR_TRANSPORT", "whatsapp"),
            inbox_endpoint=os.getenv("PROGENITOR_INBOX_ENDPOINT"),
            outbox_endpoint=os.getenv("PROGENITOR_OUTBOX_ENDPOINT"),
            allowed_senders=_env_list("PROGENITOR_ALLOWED_SENDERS"),
        )


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""

    enabled: bool = True
    poll_seconds: int = 30

    @classmethod
    def from_env(cls) -> "SchedulerConfig":
        """Load scheduler config from environment."""
        return cls(
            enabled=_env_bool("PROGENITOR_SCHEDULER_ENABLED", True),
            poll_seconds=_env_int("PROGENITOR_SCHEDULER_POLL_SECONDS", 30),
        )


@dataclass
class ProgenitorConfig:
    """Complete Progenitor configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    copilot: CopilotConfig = field(default_factory=CopilotConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)

    @classmethod
    def load(cls, project_root: Path | None = None, config_dir: Path | None = None) -> "ProgenitorConfig":
        """Load complete configuration from environment and files.

        Environment variables always override file config.
        """
        # Load JSON config files if available
        progenitor_config: dict[str, Any] = {}
        copilot_config: dict[str, Any] = {}

        if config_dir:
            progenitor_json = config_dir / "progenitor.json"
            if progenitor_json.exists():
                try:
                    with open(progenitor_json, "r") as f:
                        progenitor_config = json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to load {progenitor_json}: {e}")

            copilot_json = config_dir / "copilot.json"
            if copilot_json.exists():
                try:
                    with open(copilot_json, "r") as f:
                        copilot_config = json.load(f)
                except (json.JSONDecodeError, IOError) as e:
                    logger.warning(f"Failed to load {copilot_json}: {e}")

        return cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(progenitor_config),
            storage=StorageConfig.from_env(project_root),
            copilot=CopilotConfig.from_env(copilot_config, project_root),
            transport=TransportConfig.from_env(),
            scheduler=SchedulerConfig.from_env(),
        )

    def to_dict(self, redact_secrets: bool = True) -> dict[str, Any]:
        """Convert config to dictionary for logging."""
        result = {
            "llm": {
                "provider": self.llm.provider,
                "model_default": self.llm.model_default,
                "azure_endpoint": self.llm.azure_endpoint,
                "azure_api_version": self.llm.azure_api_version,
                "azure_deployment_default": self.llm.azure_deployment_default,
                "azure_deployment_planner": self.llm.azure_deployment_planner,
                "azure_deployment_builder": self.llm.azure_deployment_builder,
                "azure_deployment_summary": self.llm.azure_deployment_summary,
            },
            "runtime": {
                "timezone": self.runtime.timezone,
                "idle_backoff_seconds": self.runtime.idle_backoff_seconds,
                "max_actions_per_cycle": self.runtime.max_actions_per_cycle,
                "log_level": self.runtime.log_level,
                "backoff_multiplier": self.runtime.backoff_multiplier,
                "max_backoff_seconds": self.runtime.max_backoff_seconds,
                "checkpoint_interval_seconds": self.runtime.checkpoint_interval_seconds,
                "input_timeout_seconds": self.runtime.input_timeout_seconds,
            },
            "storage": {
                "state_dir": str(self.storage.state_dir),
                "log_dir": str(self.storage.log_dir),
                "worktree_dir": str(self.storage.worktree_dir),
                "state_lock_timeout_seconds": self.storage.state_lock_timeout_seconds,
            },
            "copilot": {
                "bin_path": self.copilot.bin_path,
                "config_path": str(self.copilot.config_path),
                "timeout_seconds": self.copilot.timeout_seconds,
                "default_flags": self.copilot.default_flags,
                "model": self.copilot.model,
                "max_tokens": self.copilot.max_tokens,
            },
            "transport": {
                "transport": self.transport.transport,
                "inbox_endpoint": self.transport.inbox_endpoint,
                "outbox_endpoint": self.transport.outbox_endpoint,
                "allowed_senders": self.transport.allowed_senders,
            },
            "scheduler": {
                "enabled": self.scheduler.enabled,
                "poll_seconds": self.scheduler.poll_seconds,
            },
        }

        if redact_secrets:
            # Redact API keys
            result["llm"]["openai_api_key"] = "***REDACTED***" if self.llm.openai_api_key else None
            result["llm"]["azure_api_key"] = "***REDACTED***" if self.llm.azure_api_key else None
        else:
            result["llm"]["openai_api_key"] = self.llm.openai_api_key
            result["llm"]["azure_api_key"] = self.llm.azure_api_key

        return result


# Global configuration instance (loaded lazily)
_config: Optional[ProgenitorConfig] = None


def get_config(project_root: Path | None = None, config_dir: Path | None = None) -> ProgenitorConfig:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = ProgenitorConfig.load(project_root, config_dir)
    return _config


def reload_config(project_root: Path | None = None, config_dir: Path | None = None) -> ProgenitorConfig:
    """Reload configuration (useful for testing or dynamic updates)."""
    global _config
    _config = ProgenitorConfig.load(project_root, config_dir)
    return _config


def setup_logging(config: ProgenitorConfig) -> None:
    """Configure logging based on configuration."""
    log_levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = log_levels.get(config.runtime.log_level.lower(), logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from httpx and openai
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
