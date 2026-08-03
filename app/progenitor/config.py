"""
Centralized Configuration for Progenitor.

Configuration is read from environment variables plus shared runtime state.
"""

import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

from .utils import env_bool, env_int

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)

# Default timezone for schedule evaluation when ``PROGENITOR_TIMEZONE`` is unset
# and an individual schedule entry does not override it.
DEFAULT_TIMEZONE = "Europe/London"

# ---------------------------------------------------------------------------
# Default project paths.
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_PROJECT_STATE_DIR = DEFAULT_PROJECT_DATA_DIR / "state"
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
DEFAULT_PROJECT_WORKTREE_DIR = DEFAULT_PROJECT_DATA_DIR / "repos"
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
    """Return the managed repos/worktrees directory for the current project root."""
    if worktrees_root is not None:
        return Path(worktrees_root) / "repos"
    return DEFAULT_PROJECT_WORKTREE_DIR if project_root is None else project_root / "data" / "repos"


def _default_prompts_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in prompt root unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "prompts"


def default_mcp_config_payload() -> dict[str, Any]:
    """Return the default MCP server configuration stored in the shared state document."""
    return {"mcpServers": {}}


def default_plugins_config_payload() -> dict[str, Any]:
    """Return the default plugins configuration stored in the shared state document."""
    return {"plugins": {}}


def default_marketplaces_config_payload() -> dict[str, Any]:
    """Return the default marketplaces configuration stored in the shared state document."""
    return {"marketplaces": {}}


def default_repos_config_payload() -> dict[str, Any]:
    """Return the default repos configuration stored in the shared state document."""
    return {"repos": {}}


def default_schedule_config_payload() -> dict[str, Any]:
    """Return the default (empty) schedule document persisted in Postgres."""
    return {}


@dataclass
class LLMConfig:
    """LLM provider configuration.

    All LLM calls are routed directly to Azure OpenAI Responses API.
    """

    # Azure OpenAI base URL (without trailing /responses)
    base_url: str = ""
    api_key: str | None = None

    model_default: str = "gpt-chat-latest"

    # Reasoning/thinking feature (for models that support it)
    reasoning_enabled: bool = True
    reasoning_effort: str = "low"
    reasoning_summary: str = "detailed"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM config from environment."""
        reasoning_effort = os.getenv("PROGENITOR_REASONING_EFFORT", "").strip()
        reasoning_summary = os.getenv("PROGENITOR_REASONING_SUMMARY", "").strip()

        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-chat-latest"),
            reasoning_enabled=env_bool("PROGENITOR_REASONING_ENABLED", True),
            reasoning_effort=reasoning_effort or cls.reasoning_effort,
            reasoning_summary=reasoning_summary or cls.reasoning_summary,
        )


@dataclass
class RuntimeConfig:
    """Runtime configuration."""

    timezone: str = DEFAULT_TIMEZONE
    idle_backoff_seconds: int = 30
    mcp_refresh_seconds: int = 900
    # Plugin update check performs network fetches per installed plugin, so it
    # must be far coarser than the MCP refresh.
    plugin_update_check_seconds: int = 86400
    # Refresh cadence for agent cards + /health. Kept at 900s to avoid hammering
    # each agent's health endpoint; the read-through Agents page tolerates this.
    registry_refresh_seconds: int = 900
    # Cadence for the heartbeat's git-status scan of declared repos.json
    # entries (branch, dirty count, commits, contributors, worktrees).
    # Local-only subprocess calls (no network -- see repos/scan.py's
    # module docstring for the separate on-demand "Fetch" action), but each
    # tick shells out per branch/commit/contributor per repo, so a large
    # declared set can add up; default to hourly rather than the old 120s.
    repo_scan_seconds: int = 3600
    log_level: str = "info"
    log_format: str = "text"  # "text" | "json" for structured logging
    checkpoint_interval_seconds: int = 30

    # Queue bounds (memory protection)
    max_input_queue_size: int = 100  # Max pending input messages
    max_output_queue_size: int = 100  # Max pending output messages

    # Persistent HTTP service settings (health, agent card, A2A callbacks, chat)
    http_host: str = "127.0.0.1"
    http_port: int = 9501
    http_public_base_url: str | None = None

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """Load runtime config from environment."""
        return cls(
            timezone=os.getenv("PROGENITOR_TIMEZONE", DEFAULT_TIMEZONE),
            idle_backoff_seconds=env_int("PROGENITOR_IDLE_BACKOFF_SECONDS", 30),
            mcp_refresh_seconds=env_int("PROGENITOR_MCP_REFRESH_SECONDS", 900),
            plugin_update_check_seconds=env_int("PROGENITOR_PLUGIN_UPDATE_CHECK_SECONDS", 86400),
            registry_refresh_seconds=env_int("PROGENITOR_REGISTRY_REFRESH_SECONDS", 900),
            repo_scan_seconds=env_int("PROGENITOR_REPO_SCAN_SECONDS", 3600),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
            checkpoint_interval_seconds=env_int("PROGENITOR_CHECKPOINT_INTERVAL_SECONDS", 30),
            # Queue bounds
            max_input_queue_size=env_int("PROGENITOR_MAX_INPUT_QUEUE_SIZE", 100),
            max_output_queue_size=env_int("PROGENITOR_MAX_OUTPUT_QUEUE_SIZE", 100),
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
        )


@dataclass
class DatabaseConfig:
    """Postgres datastore configuration."""

    url: str = ""
    setup: bool = True
    pool_min_size: int = 1
    pool_max_size: int = 10

    @property
    def enabled(self) -> bool:
        """Return whether Postgres persistence is configured."""
        return bool(self.url.strip())

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Load database config from environment."""
        return cls(
            url=os.getenv("PROGENITOR_DATABASE_URL", "").strip(),
            setup=env_bool("PROGENITOR_DATABASE_SETUP", True),
            pool_min_size=env_int("PROGENITOR_DATABASE_POOL_MIN_SIZE", 1),
            pool_max_size=env_int("PROGENITOR_DATABASE_POOL_MAX_SIZE", 10),
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
    """Remote HTTP MCP server definition loaded from the persisted MCP state."""

    name: str
    url: str
    enabled: bool = True
    transport: str = "http"
    headers: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class MCPConfig:
    """MCP (Model Context Protocol) configuration."""

    servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "MCPConfig":
        """Build MCP config from the persisted state document payload."""
        from .mcp.loader import load_mcp_servers

        return cls(servers=load_mcp_servers(payload))

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
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
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
            database=DatabaseConfig.from_env(),
            scheduler=SchedulerConfig.from_env(),
            mcp=MCPConfig(),
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
            database_url = result.get("database", {}).get("url")
            if database_url:
                result["database"]["url"] = _redact_url_password(str(database_url))
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


def _redact_url_password(url: str) -> str:
    """Redact password components from connection URLs."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    username = parts.username or ""
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port is not None else ""
    netloc = f"{username}:***REDACTED***@{hostname}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


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
            "mcp_refresh_seconds",
            config.runtime.mcp_refresh_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 86400,
        ),
        (
            "plugin_update_check_seconds",
            config.runtime.plugin_update_check_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 86400,
        ),
        (
            "registry_refresh_seconds",
            config.runtime.registry_refresh_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 86400,
        ),
        (
            "repo_scan_seconds",
            config.runtime.repo_scan_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 86400,
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
            "checkpoint_interval_seconds",
            config.runtime.checkpoint_interval_seconds,
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

    if config.database.enabled and not (
        1 <= config.database.pool_min_size <= config.database.pool_max_size <= 100
    ):
        issues.append(
            "Database Config Error: invalid pool bounds "
            f"min={config.database.pool_min_size!r} max={config.database.pool_max_size!r}"
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


def get_config(project_root: Path | None = None, validate: bool = True) -> ProgenitorConfig:
    """Get or create the global configuration instance.

    Args:
        project_root: Root directory of the project.
        validate: Whether to validate configuration at load time (default True).
                  Set to False for testing or when loading partial configs.
    """
    global _config
    if _config is None:
        _config = ProgenitorConfig.load(project_root, validate=validate)
        if validate:
            _log_validation_issues(_config)
    return _config


def reload_config(project_root: Path | None = None, validate: bool = True) -> ProgenitorConfig:
    """Reload configuration (useful for testing or dynamic updates).

    Args:
        project_root: Root directory of the project.
        validate: Whether to validate configuration at load time (default True).
    """
    global _config
    _config = ProgenitorConfig.load(project_root, validate=validate)
    if validate:
        _log_validation_issues(_config)
    return _config
