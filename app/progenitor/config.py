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

from .env_utils import env_bool, env_int, env_float, env_list

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)


# Keep local aliases for backward compatibility within this module
_env_bool = env_bool
_env_int = env_int
_env_float = env_float
_env_list = env_list


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
    log_format: str = "text"  # "text" | "json" for structured logging
    backoff_multiplier: float = 1.5
    max_backoff_seconds: int = 300
    checkpoint_interval_seconds: int = 30
    input_timeout_seconds: int = 300

    # Queue bounds (memory protection)
    max_input_queue_size: int = 100  # Max pending input messages
    max_output_queue_size: int = 100  # Max pending output messages
    max_stream_queue_size: int = 1000  # Max pending stream chunks

    # Health check settings
    health_stale_seconds: int = 120  # Consider unhealthy if not updated in 2 minutes

    # IPC settings (socket-based communication with backend)
    ipc_enabled: bool = True  # Enable socket IPC (vs stdin/stdout)
    ipc_host: str = "127.0.0.1"  # IPC server bind host
    ipc_port: int = 9500  # IPC server bind port
    ipc_max_clients: int = 10  # Maximum concurrent IPC clients

    # Inner loop (dual loop architecture) settings
    max_inner_iterations: int = 5  # Failsafe max iterations for inner loop
    min_verification_score: float = 0.8  # Default minimum score to pass verification
    iteration_time_limit_seconds: int = 600  # Max time per iteration

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
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", fc.get("log_format", "text")),
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
            # Queue bounds
            max_input_queue_size=_env_int(
                "PROGENITOR_MAX_INPUT_QUEUE_SIZE", fc.get("max_input_queue_size", 100)
            ),
            max_output_queue_size=_env_int(
                "PROGENITOR_MAX_OUTPUT_QUEUE_SIZE", fc.get("max_output_queue_size", 100)
            ),
            max_stream_queue_size=_env_int(
                "PROGENITOR_MAX_STREAM_QUEUE_SIZE", fc.get("max_stream_queue_size", 1000)
            ),
            # Health check settings
            health_stale_seconds=_env_int(
                "PROGENITOR_HEALTH_STALE_SECONDS", fc.get("health_stale_seconds", 120)
            ),
            # IPC settings
            ipc_enabled=_env_bool(
                "PROGENITOR_IPC_ENABLED", fc.get("ipc_enabled", True)
            ),
            ipc_host=os.getenv(
                "PROGENITOR_IPC_HOST", fc.get("ipc_host", "127.0.0.1")
            ),
            ipc_port=_env_int(
                "PROGENITOR_IPC_PORT", fc.get("ipc_port", 9500)
            ),
            ipc_max_clients=_env_int(
                "PROGENITOR_IPC_MAX_CLIENTS", fc.get("ipc_max_clients", 10)
            ),
            # Inner loop settings
            max_inner_iterations=_env_int(
                "PROGENITOR_MAX_INNER_ITERATIONS", fc.get("max_inner_iterations", 5)
            ),
            min_verification_score=_env_float(
                "PROGENITOR_MIN_VERIFICATION_SCORE", fc.get("min_verification_score", 0.8)
            ),
            iteration_time_limit_seconds=_env_int(
                "PROGENITOR_ITERATION_TIME_LIMIT_SECONDS", fc.get("iteration_time_limit_seconds", 600)
            ),
        )


@dataclass
class StorageConfig:
    """Storage paths configuration."""

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    prompts_dir: Path = field(default_factory=lambda: Path("/data/prompts"))
    state_lock_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
        """Load storage config from environment."""
        # Default to project-relative paths if not in container
        default_state = "/data/state" if project_root is None else str(project_root / "data" / "state")
        default_logs = "/logs" if project_root is None else str(project_root / "logs")
        default_worktrees = "/worktrees" if project_root is None else str(project_root / "worktrees")
        default_prompts = "/data/prompts" if project_root is None else str(project_root / "data" / "prompts")

        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", default_state)),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", default_logs)),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", default_worktrees)),
            prompts_dir=Path(os.getenv("PROGENITOR_PROMPTS_DIR", default_prompts)),
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
class TelemetryConfig:
    """Telemetry and observability configuration."""

    enabled: bool = True
    output_dir: Optional[Path] = None  # None = use storage.log_dir / "telemetry"
    console_export: bool = False  # Also print to console (debugging)
    metrics_interval_seconds: float = 15.0  # How often to snapshot metrics
    traces_file: str = "traces.jsonl"
    metrics_file: str = "metrics.jsonl"

    @classmethod
    def from_env(cls, storage_config: Optional["StorageConfig"] = None) -> "TelemetryConfig":
        """Load telemetry config from environment."""
        output_dir_str = os.getenv("PROGENITOR_TELEMETRY_DIR")
        if output_dir_str:
            output_dir = Path(output_dir_str)
        elif storage_config:
            output_dir = storage_config.log_dir / "telemetry"
        else:
            output_dir = None

        return cls(
            enabled=_env_bool("PROGENITOR_TELEMETRY_ENABLED", True),
            output_dir=output_dir,
            console_export=_env_bool("PROGENITOR_TELEMETRY_CONSOLE", False),
            metrics_interval_seconds=_env_float(
                "PROGENITOR_TELEMETRY_METRICS_INTERVAL", 15.0
            ),
            traces_file=os.getenv("PROGENITOR_TELEMETRY_TRACES_FILE", "traces.jsonl"),
            metrics_file=os.getenv("PROGENITOR_TELEMETRY_METRICS_FILE", "metrics.jsonl"),
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
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)

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

        storage_config = StorageConfig.from_env(project_root)

        config = cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(progenitor_config),
            storage=storage_config,
            copilot=CopilotConfig.from_env(copilot_config, project_root),
            transport=TransportConfig.from_env(),
            scheduler=SchedulerConfig.from_env(),
            telemetry=TelemetryConfig.from_env(storage_config),
        )

        # Validate configuration
        config.validate()

        return config

    def validate(self) -> None:
        """Validate configuration and raise errors for invalid combinations.

        Raises:
            ValueError: If configuration is invalid
        """
        errors = []

        # Validate LLM configuration
        if self.llm.provider == "azure":
            if not self.llm.azure_endpoint:
                errors.append("Azure provider requires AZURE_OPENAI_ENDPOINT")
            if not self.llm.azure_deployment_default:
                errors.append("Azure provider requires AZURE_OPENAI_DEPLOYMENT_DEFAULT")
            if not (self.llm.azure_api_key or self.llm.openai_api_key):
                errors.append("Azure provider requires AZURE_OPENAI_API_KEY or OPENAI_API_KEY")
        elif self.llm.provider == "openai":
            if not self.llm.openai_api_key:
                errors.append("OpenAI provider requires OPENAI_API_KEY")

        # Validate storage paths are sensible
        for name, path in [
            ("state_dir", self.storage.state_dir),
            ("log_dir", self.storage.log_dir),
            ("worktree_dir", self.storage.worktree_dir),
        ]:
            if not path:
                errors.append(f"Storage {name} cannot be empty")

        # Log warnings but don't fail
        if self.runtime.max_inner_iterations < 1:
            logger.warning("max_inner_iterations should be >= 1, got %d", self.runtime.max_inner_iterations)

        if self.runtime.min_verification_score < 0 or self.runtime.min_verification_score > 1:
            logger.warning("min_verification_score should be between 0 and 1, got %f", self.runtime.min_verification_score)

        if errors:
            raise ValueError("Configuration validation failed:\n  - " + "\n  - ".join(errors))

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
                "ipc_enabled": self.runtime.ipc_enabled,
                "ipc_host": self.runtime.ipc_host,
                "ipc_port": self.runtime.ipc_port,
                "ipc_max_clients": self.runtime.ipc_max_clients,
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
            "telemetry": {
                "enabled": self.telemetry.enabled,
                "output_dir": str(self.telemetry.output_dir) if self.telemetry.output_dir else None,
                "console_export": self.telemetry.console_export,
                "metrics_interval_seconds": self.telemetry.metrics_interval_seconds,
                "traces_file": self.telemetry.traces_file,
                "metrics_file": self.telemetry.metrics_file,
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


def get_config(
    project_root: Path | None = None,
    config_dir: Path | None = None,
    validate: bool = True
) -> ProgenitorConfig:
    """Get or create the global configuration instance.

    Args:
        project_root: Root directory of the project.
        config_dir: Directory containing config files.
        validate: Whether to validate configuration at load time (default True).
                  Set to False for testing or when loading partial configs.
    """
    global _config
    if _config is None:
        _config = ProgenitorConfig.load(project_root, config_dir)
        if validate:
            from .config_validation import validate_config
            is_valid, issues = validate_config(_config)
            if not is_valid:
                logger.warning(
                    "Configuration has %d validation issue(s): %s",
                    len(issues),
                    "; ".join(issues)
                )
    return _config


def reload_config(
    project_root: Path | None = None,
    config_dir: Path | None = None,
    validate: bool = True
) -> ProgenitorConfig:
    """Reload configuration (useful for testing or dynamic updates).

    Args:
        project_root: Root directory of the project.
        config_dir: Directory containing config files.
        validate: Whether to validate configuration at load time (default True).
    """
    global _config
    _config = ProgenitorConfig.load(project_root, config_dir)
    if validate:
        from .config_validation import validate_config
        is_valid, issues = validate_config(_config)
        if not is_valid:
            logger.warning(
                "Configuration has %d validation issue(s): %s",
                len(issues),
                "; ".join(issues)
            )
    return _config


def setup_logging(config: ProgenitorConfig) -> None:
    """Configure logging based on configuration.

    Supports two formats:
    - "text": Human-readable format (default)
    - "json": Structured JSON format for production/log aggregation
    """
    log_levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = log_levels.get(config.runtime.log_level.lower(), logging.INFO)

    # Choose format based on config
    if config.runtime.log_format.lower() == "json":
        # JSON structured logging for production
        formatter = JsonLogFormatter()
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logging.root.handlers = [handler]
        logging.root.setLevel(level)
    else:
        # Standard text format
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # Reduce noise from httpx and openai
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class JsonLogFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        import json as json_module
        from datetime import datetime, timezone

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields if any
        for key in ("run_id", "cycle_count", "node", "work_item_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json_module.dumps(log_entry)
