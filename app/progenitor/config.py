"""
Centralized Configuration for Progenitor.

Configuration is read from environment variables and live config files.
"""

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from .utils import env_bool, env_float, env_int

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default project paths.
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_PROJECT_STATE_DIR = DEFAULT_PROJECT_DATA_DIR / "state"
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
DEFAULT_PROJECT_WORKTREE_DIR = Path(__file__).resolve().parents[2] / "worktrees"
DEFAULT_PROJECT_PROMPTS_DIR = DEFAULT_PROJECT_DATA_DIR / "prompts"


def _default_data_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in data directory unless an explicit project root is provided."""
    return DEFAULT_PROJECT_DATA_DIR if project_root is None else project_root / "data"


def _default_state_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in state directory unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "state"


def _default_log_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in logs directory unless an explicit project root is provided."""
    return DEFAULT_PROJECT_LOG_DIR if project_root is None else project_root / "logs"


def _default_worktree_dir(
    project_root: Path | None = None,
    *,
    worktrees_root: Path | str | None = None,
) -> Path:
    """Return the managed worktree directory for the current project root."""
    if worktrees_root is not None:
        return Path(worktrees_root) / "worktrees"
    return DEFAULT_PROJECT_WORKTREE_DIR if project_root is None else project_root / "worktrees"


def _default_prompts_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in prompt root unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "prompts"


def _default_config_dir(project_root: Path | None = None) -> Path:
    """Return the live config directory derived from the active data/state roots."""
    data_dir = Path(os.getenv("PROGENITOR_DATA_DIR", str(_default_data_dir(project_root))))
    default_state_dir = data_dir / "state"
    state_dir = Path(os.getenv("PROGENITOR_STATE_DIR", str(default_state_dir)))
    return state_dir.parent / "config"


def resolve_mcp_config_path(config_dir: Path | str | None = None) -> Path:
    """Resolve the live MCP config path from the active config directory."""
    base_dir = Path(config_dir) if config_dir is not None else _default_config_dir()
    return base_dir / "mcp.json"


def resolve_schedule_config_path(config_dir: Path | str | None = None) -> Path:
    """Resolve the live schedule config path from the active config directory."""
    base_dir = Path(config_dir) if config_dir is not None else _default_config_dir()
    return base_dir / "schedule.json"


def default_mcp_config_payload() -> dict[str, Any]:
    """Return the default MCP server configuration written to data/config/mcp.json."""
    return {"mcpServers": {}}


def default_schedule_config_payload() -> list[Any]:
    """Return the default schedule configuration written to data/config/schedule.json."""
    return []


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

    # Reasoning/thinking feature (for models that support it)
    reasoning_enabled: bool = True
    reasoning_effort: str = "low"
    reasoning_summary: str = "auto"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM config from environment."""
        reasoning_effort = os.getenv("PROGENITOR_REASONING_EFFORT", "").strip()
        reasoning_summary = os.getenv("PROGENITOR_REASONING_SUMMARY", "").strip()

        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"),
            reasoning_enabled=env_bool("PROGENITOR_REASONING_ENABLED", True),
            reasoning_effort=reasoning_effort or cls.reasoning_effort,
            reasoning_summary=reasoning_summary or cls.reasoning_summary,
        )


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

    # Persistent HTTP service settings (health, agent card, A2A callbacks)
    http_host: str = "127.0.0.1"
    http_port: int = 9501
    http_public_base_url: str | None = None

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """Load runtime config from environment."""
        return cls(
            timezone=os.getenv("PROGENITOR_TIMEZONE", "Australia/Melbourne"),
            idle_backoff_seconds=env_int("PROGENITOR_IDLE_BACKOFF_SECONDS", 30),
            max_actions_per_cycle=env_int("PROGENITOR_MAX_ACTIONS_PER_CYCLE", 10),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
            backoff_multiplier=env_float("PROGENITOR_BACKOFF_MULTIPLIER", 1.5),
            max_backoff_seconds=env_int("PROGENITOR_MAX_BACKOFF_SECONDS", 300),
            checkpoint_interval_seconds=env_int("PROGENITOR_CHECKPOINT_INTERVAL_SECONDS", 30),
            input_timeout_seconds=env_int("PROGENITOR_INPUT_TIMEOUT_SECONDS", 300),
            # Queue bounds
            max_input_queue_size=env_int("PROGENITOR_MAX_INPUT_QUEUE_SIZE", 100),
            max_output_queue_size=env_int("PROGENITOR_MAX_OUTPUT_QUEUE_SIZE", 100),
            max_stream_queue_size=env_int("PROGENITOR_MAX_STREAM_QUEUE_SIZE", 1000),
            # Health check settings
            health_stale_seconds=env_int("PROGENITOR_HEALTH_STALE_SECONDS", 120),
            # IPC settings
            ipc_enabled=env_bool("PROGENITOR_IPC_ENABLED", True),
            ipc_host=os.getenv("PROGENITOR_IPC_HOST", "127.0.0.1"),
            ipc_port=env_int("PROGENITOR_IPC_PORT", 9500),
            ipc_max_clients=env_int("PROGENITOR_IPC_MAX_CLIENTS", 10),
            http_host=os.getenv("PROGENITOR_HTTP_HOST", "127.0.0.1"),
            http_port=env_int("PROGENITOR_HTTP_PORT", 9501),
            http_public_base_url=os.getenv("PROGENITOR_HTTP_PUBLIC_BASE_URL") or None,
        )


@dataclass
class StorageConfig:
    """Storage paths configuration."""

    data_dir: Path = field(default_factory=_default_data_dir)
    state_dir: Path = field(default_factory=_default_state_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    worktree_dir: Path = field(default_factory=_default_worktree_dir)
    prompts_dir: Path = field(default_factory=_default_prompts_dir)
    state_lock_timeout_seconds: int = 10

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
        """Load storage config from environment."""
        worktrees_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        default_data = Path(os.getenv("PROGENITOR_DATA_DIR", str(_default_data_dir(project_root))))
        default_state = default_data / "state"
        default_logs = Path(os.getenv("PROGENITOR_LOG_DIR", str(_default_log_dir(project_root))))
        default_worktrees = Path(
            os.getenv(
                "PROGENITOR_WORKTREE_DIR",
                str(_default_worktree_dir(project_root, worktrees_root=worktrees_root or None)),
            )
        )
        default_prompts = default_data / "prompts"

        return cls(
            data_dir=default_data,
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(default_state))),
            log_dir=default_logs,
            worktree_dir=default_worktrees,
            prompts_dir=Path(os.getenv("PROGENITOR_PROMPTS_DIR", str(default_prompts))),
            state_lock_timeout_seconds=env_int("PROGENITOR_STATE_LOCK_TIMEOUT_SECONDS", 10),
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
class MCPServerConfig:
    """Remote HTTP MCP server definition loaded from the live MCP config file."""

    name: str
    url: str
    enabled: bool = True
    transport: str = "http"
    headers: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class MCPConfig:
    """MCP (Model Context Protocol) configuration."""

    config_path: Path = field(default_factory=resolve_mcp_config_path)
    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        *,
        config_path: Path | str | None = None,
    ) -> "MCPConfig":
        """Load MCP config from the live data/config/mcp.json file."""
        resolved_config_path = (
            Path(config_path) if config_path is not None else resolve_mcp_config_path()
        )
        from .mcp.loader import load_mcp_servers

        return cls(
            config_path=resolved_config_path,
            servers=load_mcp_servers(resolved_config_path),
        )

    @property
    def enabled_servers(self) -> list[MCPServerConfig]:
        """Return the remote HTTP MCP servers that should be active."""
        return [server for server in self.servers if server.enabled]


@dataclass
class ProgenitorConfig:
    """Complete Progenitor configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        _config_dir: Path | None = None,
        *,
        validate: bool = True,
    ) -> "ProgenitorConfig":
        """Load complete configuration from environment and files.

        Configuration is sourced from environment variables.
        """
        storage_config = StorageConfig.from_env(project_root)

        config = cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(),
            storage=storage_config,
            scheduler=SchedulerConfig.from_env(),
            mcp=MCPConfig.load(),
        )

        if validate:
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

        if errors:
            raise ValueError("Configuration validation failed:\n  - " + "\n  - ".join(errors))

    def to_dict(self, redact_secrets: bool = True) -> dict[str, Any]:
        """Convert config to a JSON-safe dictionary for logging."""
        result = cast(dict[str, Any], _json_safe(asdict(self)))

        if redact_secrets:
            result["llm"]["api_key"] = "***REDACTED***" if self.llm.api_key else None
            for server in result["mcp"]["servers"]:
                headers = server.get("headers")
                if isinstance(headers, dict):
                    server["headers"] = {
                        key: "***REDACTED***" if value else value for key, value in headers.items()
                    }
        else:
            result["llm"]["api_key"] = self.llm.api_key

        return result


def _json_safe(value: Any) -> Any:
    """Convert dataclass/asdict output to JSON-safe primitives."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _collect_validation_issues(config: ProgenitorConfig) -> list[str]:
    """Collect non-fatal configuration issues for startup diagnostics."""
    issues: list[str] = []

    if not config.llm.base_url:
        issues.append("LLM Config Error: base_url must not be empty")
    if not config.llm.model_default:
        issues.append("LLM Config Error: model_default must not be empty")

    runtime_rules = [
        ("timezone", config.runtime.timezone, lambda v: isinstance(v, str) and len(v) >= 1),
        (
            "idle_backoff_seconds",
            config.runtime.idle_backoff_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 3600,
        ),
        (
            "max_actions_per_cycle",
            config.runtime.max_actions_per_cycle,
            lambda v: isinstance(v, int) and 0 < v <= 100,
        ),
        (
            "log_level",
            config.runtime.log_level,
            lambda v: isinstance(v, str) and v in {"debug", "info", "warn", "warning", "error"},
        ),
        (
            "log_format",
            config.runtime.log_format,
            lambda v: isinstance(v, str) and v in {"text", "json"},
        ),
        (
            "backoff_multiplier",
            config.runtime.backoff_multiplier,
            lambda v: isinstance(v, (int, float)) and 1.0 < float(v) <= 10.0,
        ),
        (
            "max_backoff_seconds",
            config.runtime.max_backoff_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 3600,
        ),
        (
            "checkpoint_interval_seconds",
            config.runtime.checkpoint_interval_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 3600,
        ),
        (
            "input_timeout_seconds",
            config.runtime.input_timeout_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 3600,
        ),
        (
            "max_input_queue_size",
            config.runtime.max_input_queue_size,
            lambda v: isinstance(v, int) and 0 < v <= 10000,
        ),
        (
            "max_output_queue_size",
            config.runtime.max_output_queue_size,
            lambda v: isinstance(v, int) and 0 < v <= 10000,
        ),
        (
            "max_stream_queue_size",
            config.runtime.max_stream_queue_size,
            lambda v: isinstance(v, int) and 0 < v <= 100000,
        ),
        (
            "health_stale_seconds",
            config.runtime.health_stale_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 600,
        ),
    ]
    for field_name, value, predicate in runtime_rules:
        if not predicate(value):
            issues.append(f"Runtime Config Error: invalid {field_name}={value!r}")

    storage_rules = [
        ("state_dir", config.storage.state_dir),
        ("log_dir", config.storage.log_dir),
        ("worktree_dir", config.storage.worktree_dir),
        ("prompts_dir", config.storage.prompts_dir),
    ]
    for field_name, value in storage_rules:
        if not isinstance(value, Path):
            issues.append(f"Storage Config Error: {field_name} must be a Path")
    if not (0 < config.storage.state_lock_timeout_seconds <= 300):
        issues.append(
            "Storage Config Error: invalid "
            f"state_lock_timeout_seconds={config.storage.state_lock_timeout_seconds!r}"
        )

    if not (0 < config.scheduler.poll_seconds <= 3600):
        issues.append(
            f"Scheduler Config Error: invalid poll_seconds={config.scheduler.poll_seconds!r}"
        )

    return issues


def _log_validation_issues(config: ProgenitorConfig) -> None:
    """Emit warning logs for non-fatal configuration issues."""
    issues = _collect_validation_issues(config)
    if issues:
        logger.warning(
            "Configuration has %d validation issue(s): %s", len(issues), "; ".join(issues)
        )


# Global configuration instance (loaded lazily)
_config: ProgenitorConfig | None = None


def get_config(
    project_root: Path | None = None, config_dir: Path | None = None, validate: bool = True
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
        _config = ProgenitorConfig.load(project_root, config_dir, validate=validate)
        if validate:
            _log_validation_issues(_config)
    return _config


def reload_config(
    project_root: Path | None = None, config_dir: Path | None = None, validate: bool = True
) -> ProgenitorConfig:
    """Reload configuration (useful for testing or dynamic updates).

    Args:
        project_root: Root directory of the project.
        config_dir: Directory containing config files.
        validate: Whether to validate configuration at load time (default True).
    """
    global _config
    _config = ProgenitorConfig.load(project_root, config_dir, validate=validate)
    if validate:
        _log_validation_issues(_config)
    return _config
