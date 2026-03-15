"""
Centralized Configuration for Progenitor.

Configuration is read from environment variables. Per SPEC.md.
"""

import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .utils import env_bool, env_float, env_int, env_list

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)


def _first_env(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


# ---------------------------------------------------------------------------
# Backward-compatible default constants
#
# These values historically lived in app/progenitor/constants.py. They are
# configuration defaults (not protocol constants), so they now live here.
# ---------------------------------------------------------------------------

DEFAULT_MAX_INNER_ITERATIONS = 5
DEFAULT_MAX_OUTER_ITERATIONS = 10
DEFAULT_BUILD_CONFIDENCE_THRESHOLD = 0.95
DEFAULT_VERIFICATION_TARGET_SCORE = 0.8
DEFAULT_MAX_CONVERSATION_HISTORY = 10


@dataclass
class LLMConfig:
    """LLM provider configuration.

    All LLM calls are routed directly to Azure OpenAI Responses API.
    """

    # Azure OpenAI base URL (without trailing /responses)
    base_url: str = ""
    api_key: str | None = None

    # Model selection
    model_default: str = "gpt-5.1-codex-max"

    # Per-role model overrides (optional)
    model_planner: str | None = None
    model_builder: str | None = None
    model_summary: str | None = None
    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM config from environment."""
        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"),
            model_planner=os.getenv("PROGENITOR_MODEL_PLANNER"),
            model_builder=os.getenv("PROGENITOR_MODEL_BUILDER"),
            model_summary=os.getenv("PROGENITOR_MODEL_SUMMARY"),
        )

    def get_model_for_role(self, role: str) -> str:
        """Get model name for a specific role."""
        role_models = {
            "planner": self.model_planner,
            "builder": self.model_builder,
            "summary": self.model_summary,
        }
        return role_models.get(role) or self.model_default


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

    # Decision routing settings
    build_confidence_threshold: float = 0.95  # Min confidence for build actions

    # Build concurrency settings
    max_build_concurrency: int = 3  # Max concurrent builds (adaptive to backlog size)

    @classmethod
    def from_env(cls, file_config: dict[str, Any] | None = None) -> "RuntimeConfig":
        """Load runtime config from environment with file fallbacks."""
        fc = file_config or {}
        return cls(
            timezone=os.getenv("PROGENITOR_TIMEZONE", fc.get("timezone", "Australia/Melbourne")),
            idle_backoff_seconds=env_int(
                "PROGENITOR_IDLE_BACKOFF_SECONDS", fc.get("idle_backoff_seconds", 30)
            ),
            max_actions_per_cycle=env_int(
                "PROGENITOR_MAX_ACTIONS_PER_CYCLE", fc.get("max_actions_per_cycle", 10)
            ),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", fc.get("log_level", "info")),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", fc.get("log_format", "text")),
            backoff_multiplier=env_float(
                "PROGENITOR_BACKOFF_MULTIPLIER", fc.get("backoff_multiplier", 1.5)
            ),
            max_backoff_seconds=env_int(
                "PROGENITOR_MAX_BACKOFF_SECONDS", fc.get("max_backoff_seconds", 300)
            ),
            checkpoint_interval_seconds=env_int(
                "PROGENITOR_CHECKPOINT_INTERVAL_SECONDS", fc.get("checkpoint_interval_seconds", 30)
            ),
            input_timeout_seconds=env_int(
                "PROGENITOR_INPUT_TIMEOUT_SECONDS", fc.get("input_timeout_seconds", 300)
            ),
            # Queue bounds
            max_input_queue_size=env_int(
                "PROGENITOR_MAX_INPUT_QUEUE_SIZE", fc.get("max_input_queue_size", 100)
            ),
            max_output_queue_size=env_int(
                "PROGENITOR_MAX_OUTPUT_QUEUE_SIZE", fc.get("max_output_queue_size", 100)
            ),
            max_stream_queue_size=env_int(
                "PROGENITOR_MAX_STREAM_QUEUE_SIZE", fc.get("max_stream_queue_size", 1000)
            ),
            # Health check settings
            health_stale_seconds=env_int(
                "PROGENITOR_HEALTH_STALE_SECONDS", fc.get("health_stale_seconds", 120)
            ),
            # IPC settings
            ipc_enabled=env_bool(
                "PROGENITOR_IPC_ENABLED", fc.get("ipc_enabled", True)
            ),
            ipc_host=os.getenv(
                "PROGENITOR_IPC_HOST", fc.get("ipc_host", "127.0.0.1")
            ),
            ipc_port=env_int(
                "PROGENITOR_IPC_PORT", fc.get("ipc_port", 9500)
            ),
            ipc_max_clients=env_int(
                "PROGENITOR_IPC_MAX_CLIENTS", fc.get("ipc_max_clients", 10)
            ),
            # Inner loop settings
            max_inner_iterations=env_int(
                "PROGENITOR_MAX_INNER_ITERATIONS", fc.get("max_inner_iterations", 5)
            ),
            min_verification_score=env_float(
                "PROGENITOR_MIN_VERIFICATION_SCORE", fc.get("min_verification_score", 0.8)
            ),
            iteration_time_limit_seconds=env_int(
                "PROGENITOR_ITERATION_TIME_LIMIT_SECONDS", fc.get("iteration_time_limit_seconds", 600)
            ),
            # Decision routing settings
            build_confidence_threshold=env_float(
                "PROGENITOR_BUILD_CONFIDENCE_THRESHOLD", fc.get("build_confidence_threshold", 0.95)
            ),
            # Build concurrency settings
            max_build_concurrency=env_int(
                "PROGENITOR_MAX_BUILD_CONCURRENCY", fc.get("max_build_concurrency", 3)
            ),
        )


@dataclass
class StorageConfig:
    """Storage paths configuration."""

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    prompts_dir: Path = field(default_factory=lambda: Path("/prompts"))
    state_lock_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
        """Load storage config from environment."""
        # Default to project-relative paths if not in container
        default_state = "/data/state" if project_root is None else str(project_root / "data" / "state")
        default_logs = "/logs" if project_root is None else str(project_root / "logs")
        default_worktrees = "/worktrees" if project_root is None else str(project_root / "worktrees")
        default_prompts = "/prompts" if project_root is None else str(project_root / "prompts")

        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", default_state)),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", default_logs)),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", default_worktrees)),
            prompts_dir=Path(os.getenv("PROGENITOR_PROMPTS_DIR", default_prompts)),
            state_lock_timeout_seconds=env_int("PROGENITOR_STATE_LOCK_TIMEOUT_SECONDS", 10),
        )


@dataclass
class CopilotConfig:
    """Copilot CLI configuration."""

    bin_path: str = "copilot"
    timeout_seconds: int = 1200  # 20 minutes per ACP request
    default_flags: list[str] = field(default_factory=lambda: ["--yolo"])
    model: str = "gpt-5.3-codex"
    max_tokens: int = 8192

    # ACP (Agent Client Protocol) settings
    acp_port: int = 3000  # Port for the ACP server
    acp_allowed_dirs: list[str] = field(default_factory=list)  # Restricted directories

    @classmethod
    def from_env(cls) -> "CopilotConfig":
        """Load Copilot config from environment variables."""
        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", "copilot"),
            timeout_seconds=env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", 1200),
            default_flags=env_list("PROGENITOR_COPILOT_DEFAULT_FLAGS", ["--yolo"]),
            model=_first_env(
                "DEFAULT_COPILOT_MODEL",
                "PROGENITOR_COPILOT_MODEL",
                default="gpt-5.3-codex",
            ) or "gpt-5.3-codex",
            max_tokens=env_int("PROGENITOR_COPILOT_MAX_TOKENS", 8192),
            # ACP settings
            acp_port=env_int("PROGENITOR_COPILOT_ACP_PORT", 3000),
            acp_allowed_dirs=env_list("PROGENITOR_COPILOT_ACP_ALLOWED_DIRS", []),
        )


@dataclass
class ValidationConfig:
    """Validation configuration loaded from .env."""

    # Thresholds
    execution_warning_ms: int = 60000
    response_warning_ms: int = 10000
    max_errors_to_show: int = 10
    max_failed_prompts_to_show: int = 5

    # Feature flags
    enable_pattern_detection: bool = True
    enable_deduplication: bool = True
    enable_historical_comparison: bool = True
    enable_llm_severity: bool = True
    enable_python_tool_actions: bool = True

    # Python tool settings
    pytest_timeout_seconds: int = 120
    pyright_timeout_seconds: int = 60
    mypy_timeout_seconds: int = 60
    ruff_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "ValidationConfig":
        """Load validation config from environment variables."""
        return cls(
            execution_warning_ms=env_int("PROGENITOR_VALIDATION_EXECUTION_WARNING_MS", 60000),
            response_warning_ms=env_int("PROGENITOR_VALIDATION_RESPONSE_WARNING_MS", 10000),
            max_errors_to_show=env_int("PROGENITOR_VALIDATION_MAX_ERRORS", 10),
            max_failed_prompts_to_show=env_int("PROGENITOR_VALIDATION_MAX_FAILED_PROMPTS", 5),
            enable_pattern_detection=env_bool("PROGENITOR_VALIDATION_PATTERN_DETECTION", True),
            enable_deduplication=env_bool("PROGENITOR_VALIDATION_DEDUPLICATION", True),
            enable_historical_comparison=env_bool("PROGENITOR_VALIDATION_HISTORICAL_COMPARISON", True),
            enable_llm_severity=env_bool("PROGENITOR_VALIDATION_LLM_SEVERITY", True),
            enable_python_tool_actions=env_bool("PROGENITOR_VALIDATION_PYTHON_TOOLS", True),
            pytest_timeout_seconds=env_int("PROGENITOR_VALIDATION_PYTEST_TIMEOUT", 120),
            pyright_timeout_seconds=env_int("PROGENITOR_VALIDATION_PYRIGHT_TIMEOUT", 60),
            mypy_timeout_seconds=env_int("PROGENITOR_VALIDATION_MYPY_TIMEOUT", 60),
            ruff_timeout_seconds=env_int("PROGENITOR_VALIDATION_RUFF_TIMEOUT", 30),
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
            enabled=env_bool("PROGENITOR_SCHEDULER_ENABLED", True),
            poll_seconds=env_int("PROGENITOR_SCHEDULER_POLL_SECONDS", 30),
        )


@dataclass
class MCPConfig:
    """MCP (Model Context Protocol) configuration."""

    enabled: bool = True
    base_url: str = "http://localhost:8080/mcp/"
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "MCPConfig":
        """Load MCP config from environment."""
        base_url = os.getenv("PROGENITOR_MCP_BASE_URL", "http://localhost:8080/mcp/").strip()
        api_key = os.getenv("PROGENITOR_MCP_API_KEY")

        return cls(
            enabled=env_bool("PROGENITOR_MCP_ENABLED", True),
            base_url=base_url,
            api_key=api_key,
        )


@dataclass
class ProgenitorConfig:
    """Complete Progenitor configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    copilot: CopilotConfig = field(default_factory=CopilotConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def load(cls, project_root: Path | None = None, _config_dir: Path | None = None) -> "ProgenitorConfig":
        """Load complete configuration from environment and files.

        Configuration is sourced from environment variables.
        """
        storage_config = StorageConfig.from_env(project_root)

        config = cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(),
            storage=storage_config,
            copilot=CopilotConfig.from_env(),
            scheduler=SchedulerConfig.from_env(),
            validation=ValidationConfig.from_env(),
            mcp=MCPConfig.from_env(),
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
        if not self.llm.base_url:
            errors.append("AZURE_OPENAI_BASE_URL must be set")

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
        result: dict[str, Any] = {
            "llm": {
                "base_url": self.llm.base_url,
                "model_default": self.llm.model_default,
                "model_planner": self.llm.model_planner,
                "model_builder": self.llm.model_builder,
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
                "timeout_seconds": self.copilot.timeout_seconds,
                "default_flags": self.copilot.default_flags,
                "model": self.copilot.model,
                "max_tokens": self.copilot.max_tokens,
                "acp_port": self.copilot.acp_port,
                "acp_allowed_dirs": self.copilot.acp_allowed_dirs,
            },
            "scheduler": {
                "enabled": self.scheduler.enabled,
                "poll_seconds": self.scheduler.poll_seconds,
            },
            "mcp": {
                "enabled": self.mcp.enabled,
                "base_url": self.mcp.base_url,
            },
        }

        if redact_secrets:
            result["llm"]["api_key"] = "***REDACTED***" if self.llm.api_key else None
            result["mcp"]["api_key"] = "***REDACTED***" if self.mcp.api_key else None
        else:
            result["llm"]["api_key"] = self.llm.api_key
            result["mcp"]["api_key"] = self.mcp.api_key

        return result


# Global configuration instance (loaded lazily)
_config: ProgenitorConfig | None = None


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


def setup_logging(config: ProgenitorConfig, agent_name: str = "progenitor") -> None:
    """Configure logging based on configuration.

    Supports two formats:
    - "text": Human-readable format (default)
    - "json": Structured JSON format for production/log aggregation

    Writes split rotating log files under {log_dir}/{agent_name}/:
    - {log_dir}/{agent_name}/{agent_name}.stdout.log for records below ERROR
    - {log_dir}/{agent_name}/{agent_name}.stderr.log for ERROR and above

    Also emits non-error logs to stdout and errors to stderr so container logs
    preserve the same split for mounted inspection and diagnostics.
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
        formatter: logging.Formatter = JsonLogFormatter()
    else:
        # Standard text format
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    root_logger = logging.getLogger()
    for existing_handler in root_logger.handlers[:]:
        existing_handler.close()
        root_logger.removeHandler(existing_handler)
    root_logger.setLevel(level)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(_BelowErrorFilter())
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # Add rotating file handlers with the same split as stdout/stderr.
    try:
        log_dir = config.storage.log_dir / agent_name
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log_file = log_dir / f"{agent_name}.stdout.log"
        stdout_file_handler = logging.handlers.RotatingFileHandler(
            stdout_log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        stdout_file_handler.setLevel(level)
        stdout_file_handler.addFilter(_BelowErrorFilter())
        stdout_file_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_file_handler)

        stderr_log_file = log_dir / f"{agent_name}.stderr.log"
        stderr_file_handler = logging.handlers.RotatingFileHandler(
            stderr_log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        stderr_file_handler.setLevel(logging.ERROR)
        stderr_file_handler.setFormatter(formatter)
        root_logger.addHandler(stderr_file_handler)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "Could not create split log files for %s: %s", agent_name, exc
        )

    # Reduce noise from httpx and openai
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class _BelowErrorFilter(logging.Filter):
    """Allow only records below ERROR."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return True for records that should stay on stdout."""
        return record.levelno < logging.ERROR


class JsonLogFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        import json as json_module
        from datetime import datetime

        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
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
