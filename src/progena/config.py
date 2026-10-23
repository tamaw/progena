"""
Centralized Configuration for Progena.

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

# Default timezone for schedule evaluation when ``PROGENA_TIMEZONE`` is unset
# and an individual schedule entry does not override it.
DEFAULT_TIMEZONE = "Europe/London"

# ---------------------------------------------------------------------------
# Default project paths.
# ---------------------------------------------------------------------------

DEFAULT_PROJECT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_PROJECT_STATE_DIR = DEFAULT_PROJECT_DATA_DIR / "state"
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
DEFAULT_PROJECT_WORKTREE_DIR = DEFAULT_PROJECT_DATA_DIR / "repos"
DEFAULT_PROJECT_INSTRUCTIONS_DIR = DEFAULT_PROJECT_DATA_DIR / "instructions"


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


def _default_instructions_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in prompt root unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "instructions"


def _default_shim_bin_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in command-shim directory."""
    return _default_data_dir(project_root) / "shims" / "bin"


def _default_shell_home_dir(project_root: Path | None = None) -> Path:
    """Return the runtime-only home directory for shell-enabled agents."""
    return _default_data_dir(project_root) / "shell-home"


def _default_shell_workspace_dir(project_root: Path | None = None) -> Path:
    """Return writable shell scratch storage independent of repository mounts."""
    return _default_data_dir(project_root) / "shell-workspaces"


def default_mcp_config_payload() -> dict[str, Any]:
    """Return the default MCP server configuration stored in the shared state document."""
    return {"mcpServers": {}}


def default_plugins_config_payload() -> dict[str, Any]:
    """Return the default plugins configuration stored in the shared state document."""
    return {"plugins": {}}


def default_marketplaces_config_payload() -> dict[str, Any]:
    """Return the default marketplaces configuration stored in the shared state document."""
    return {"marketplaces": {}}


def default_schedule_config_payload() -> dict[str, Any]:
    """Return the default (empty) schedule document persisted in Postgres."""
    return {}


@dataclass
class LLMConfig:
    """Snapshot of one persisted Responses API provider connection.

    Connection values are resolved from ``provider_connection`` at the start of
    a turn.  Environment variables are intentionally not a runtime connection
    fallback: they are only consulted by the provider repository while seeding
    its one-time legacy connection or resolving an explicitly selected
    environment credential.
    """

    base_url: str = ""
    api_key: str | None = field(default=None, repr=False)
    model_default: str = ""
    provider_kind: str = "foundry"
    provider_id: str | None = None
    revision: int = 0

    # Reasoning/thinking feature (for models that support it)
    reasoning_enabled: bool = True
    reasoning_effort: str = "low"
    reasoning_summary: str = "detailed"

    # Model request limits. The model streams, so the timeout bounds silence
    # between chunks (connect/read/write), not the length of a whole answer; a
    # stalled request is retried up to max_retries times. The OpenAI SDK
    # defaults (600s, 2) let one stalled call hang a turn for ten minutes.
    timeout_seconds: int = 120
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load only non-connection model behaviour from environment.

        Provider credentials and endpoints live in Postgres.  Returning an
        unconfigured snapshot here allows the runtime and Settings UI to boot
        before an administrator has created a provider.
        """
        reasoning_effort = os.getenv("PROGENA_REASONING_EFFORT", "").strip()
        reasoning_summary = os.getenv("PROGENA_REASONING_SUMMARY", "").strip()
        return cls(
            reasoning_enabled=env_bool("PROGENA_REASONING_ENABLED", True),
            reasoning_effort=reasoning_effort or cls.reasoning_effort,
            reasoning_summary=reasoning_summary or cls.reasoning_summary,
            timeout_seconds=max(1, env_int("PROGENA_LLM_TIMEOUT_SECONDS", cls.timeout_seconds)),
            max_retries=max(0, env_int("PROGENA_LLM_MAX_RETRIES", cls.max_retries)),
        )

    @classmethod
    def from_provider(cls, provider: dict[str, Any], **overrides: Any) -> "LLMConfig":
        """Create a connection snapshot from ``ProviderRepository.resolve``."""
        fields = {
            "base_url": provider.get("base_url"),
            "api_key": provider.get("api_key"),
            "model_default": provider.get("model_default"),
            "provider_kind": provider.get("provider_kind"),
            "provider_id": provider.get("provider_id"),
            "revision": provider.get("revision"),
            **overrides,
        }
        return cls(**fields)


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
    # Cadence for the heartbeat's git-status scan of declared repo:<id>
    # entries (branch, dirty count, commits, contributors, worktrees).
    # Local-only subprocess calls (no network -- see repos/scan.py's
    # module docstring for the separate on-demand "Fetch" action), but each
    # tick shells out per branch/commit/contributor per repo, so a large
    # declared set can add up; default to hourly rather than the old 120s.
    repo_scan_seconds: int = 3600
    log_level: str = "info"
    log_format: str = "text"  # "text" | "json" for structured logging
    checkpoint_interval_seconds: int = 30
    # Maximum duration for one object-agent shell command.
    shell_timeout_seconds: int = 120

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
            timezone=os.getenv("PROGENA_TIMEZONE", DEFAULT_TIMEZONE),
            idle_backoff_seconds=env_int("PROGENA_IDLE_BACKOFF_SECONDS", 30),
            mcp_refresh_seconds=env_int("PROGENA_MCP_REFRESH_SECONDS", 900),
            plugin_update_check_seconds=env_int("PROGENA_PLUGIN_UPDATE_CHECK_SECONDS", 86400),
            registry_refresh_seconds=env_int("PROGENA_REGISTRY_REFRESH_SECONDS", 900),
            repo_scan_seconds=env_int("PROGENA_REPO_SCAN_SECONDS", 3600),
            log_level=os.getenv("PROGENA_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENA_LOG_FORMAT", "text"),
            checkpoint_interval_seconds=env_int("PROGENA_CHECKPOINT_INTERVAL_SECONDS", 30),
            shell_timeout_seconds=env_int("PROGENA_SHELL_TIMEOUT_SECONDS", 120),
            # Queue bounds
            max_input_queue_size=env_int("PROGENA_MAX_INPUT_QUEUE_SIZE", 100),
            max_output_queue_size=env_int("PROGENA_MAX_OUTPUT_QUEUE_SIZE", 100),
            http_host=os.getenv("PROGENA_HTTP_HOST", "127.0.0.1"),
            http_port=env_int("PROGENA_HTTP_PORT", 9501),
            http_public_base_url=os.getenv("PROGENA_HTTP_PUBLIC_BASE_URL") or None,
        )


@dataclass
class StorageConfig:
    """Storage paths configuration."""

    data_dir: Path = field(default_factory=_default_data_dir)
    state_dir: Path = field(default_factory=_default_state_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    worktree_dir: Path = field(default_factory=_default_worktree_dir)
    instructions_dir: Path = field(default_factory=_default_instructions_dir)
    agent_repositories: tuple[Path, ...] = ()
    shim_bin_dir: Path = field(default_factory=_default_shim_bin_dir)
    shell_home_dir: Path = field(default_factory=_default_shell_home_dir)
    shell_workspace_dir: Path = field(default_factory=_default_shell_workspace_dir)
    # Optional cross-container exchange volume, mounted writable at /shared/.
    shared_dir: Path | None = None

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
        """Load storage config from environment."""
        worktrees_root = (os.getenv("PROGENA_WORKTREES_ROOT") or "").strip()
        default_data = Path(os.getenv("PROGENA_DATA_DIR", str(_default_data_dir(project_root))))
        default_state = default_data / "state"
        default_logs = Path(os.getenv("PROGENA_LOG_DIR", str(_default_log_dir(project_root))))
        default_worktrees = Path(
            os.getenv(
                "PROGENA_WORKTREE_DIR",
                str(_default_worktree_dir(project_root, worktrees_root=worktrees_root or None)),
            )
        )
        default_instructions = default_data / "instructions"
        default_shim_bin = default_data / "shims" / "bin"
        default_shell_home = default_data / "shell-home"
        agent_repositories = tuple(
            Path(value.strip()).expanduser()
            for value in os.getenv("PROGENA_AGENT_REPOSITORIES", "").split(os.pathsep)
            if value.strip()
        )
        shared_dir = (os.getenv("PROGENA_SHARED_DIR") or "").strip()

        return cls(
            data_dir=default_data,
            state_dir=Path(os.getenv("PROGENA_STATE_DIR", str(default_state))),
            log_dir=default_logs,
            worktree_dir=default_worktrees,
            instructions_dir=Path(os.getenv("PROGENA_INSTRUCTIONS_DIR", str(default_instructions))),
            agent_repositories=agent_repositories,
            shim_bin_dir=Path(os.getenv("PROGENA_SHELL_SHIM_BIN", str(default_shim_bin))),
            shell_home_dir=Path(os.getenv("PROGENA_SHELL_HOME", str(default_shell_home))),
            shell_workspace_dir=Path(
                os.getenv("PROGENA_SHELL_WORKSPACE_DIR", str(default_data / "shell-workspaces"))
            ),
            shared_dir=Path(shared_dir).expanduser() if shared_dir else None,
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
            url=os.getenv("PROGENA_DATABASE_URL", "").strip(),
            setup=env_bool("PROGENA_DATABASE_SETUP", True),
            pool_min_size=env_int("PROGENA_DATABASE_POOL_MIN_SIZE", 1),
            pool_max_size=env_int("PROGENA_DATABASE_POOL_MAX_SIZE", 10),
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
            enabled=env_bool("PROGENA_SCHEDULER_ENABLED", True),
            poll_seconds=env_int("PROGENA_SCHEDULER_POLL_SECONDS", 30),
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
class DaytonaConfig:
    """Host-only credentials and bounded disposable-sandbox execution policy."""

    api_key: str = field(default="", repr=False)
    api_url: str | None = None
    target: str | None = None
    snapshot: str | None = None
    timeout_seconds: int = 120
    network_block_all: bool = True

    @classmethod
    def from_env(cls) -> "DaytonaConfig":
        return cls(
            api_key=os.getenv("DAYTONA_API_KEY", "").strip(),
            api_url=os.getenv("DAYTONA_API_URL", "").strip() or None,
            target=os.getenv("DAYTONA_TARGET", "").strip() or None,
            snapshot=os.getenv("PROGENA_DAYTONA_SNAPSHOT", "").strip() or None,
            timeout_seconds=env_int("PROGENA_DAYTONA_TIMEOUT_SECONDS", 120),
            network_block_all=env_bool("PROGENA_DAYTONA_NETWORK_BLOCK_ALL", True),
        )


@dataclass
class ProgenaConfig:
    """Complete Progena configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    daytona: DaytonaConfig = field(default_factory=DaytonaConfig)

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        *,
        validate: bool = True,
    ) -> "ProgenaConfig":
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
            daytona=DaytonaConfig.from_env(),
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

        # Validate storage paths are sensible
        for name, path in [
            ("state_dir", self.storage.state_dir),
            ("log_dir", self.storage.log_dir),
            ("worktree_dir", self.storage.worktree_dir),
        ]:
            if not path:
                errors.append(f"Storage {name} cannot be empty")
        if not 1 <= self.runtime.shell_timeout_seconds <= 3600:
            errors.append("PROGENA_SHELL_TIMEOUT_SECONDS must be between 1 and 3600")
        if not 1 <= self.daytona.timeout_seconds <= 300:
            errors.append("PROGENA_DAYTONA_TIMEOUT_SECONDS must be between 1 and 300")
        if self.daytona.api_url:
            endpoint = urlsplit(self.daytona.api_url)
            if endpoint.scheme not in {"http", "https"} or not endpoint.netloc or endpoint.username:
                errors.append("DAYTONA_API_URL must be an HTTP(S) URL without embedded credentials")

        if errors:
            raise ValueError("Configuration validation failed:\n  - " + "\n  - ".join(errors))

    def to_dict(self, redact_secrets: bool = True) -> dict[str, Any]:
        """Convert config to a JSON-safe dictionary for logging."""
        result = cast(dict[str, Any], _json_safe(asdict(self)))

        if redact_secrets:
            result["llm"]["api_key"] = "***REDACTED***" if self.llm.api_key else None
            result["daytona"]["api_key"] = "***REDACTED***" if self.daytona.api_key else None
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


def _collect_validation_issues(config: ProgenaConfig) -> list[str]:
    """Collect non-fatal configuration issues for startup diagnostics."""
    issues: list[str] = []

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
        ("instructions_dir", config.storage.instructions_dir),
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


def _log_validation_issues(config: ProgenaConfig) -> None:
    """Emit warning logs for non-fatal configuration issues."""
    issues = _collect_validation_issues(config)
    if issues:
        logger.warning(
            "Configuration has %d validation issue(s): %s", len(issues), "; ".join(issues)
        )


# Global configuration instance (loaded lazily)
_config: ProgenaConfig | None = None


def get_config(project_root: Path | None = None, validate: bool = True) -> ProgenaConfig:
    """Get or create the global configuration instance.

    Args:
        project_root: Root directory of the project.
        validate: Whether to validate configuration at load time (default True).
                  Set to False for testing or when loading partial configs.
    """
    global _config
    if _config is None:
        _config = ProgenaConfig.load(project_root, validate=validate)
        if validate:
            _log_validation_issues(_config)
    return _config


def reload_config(project_root: Path | None = None, validate: bool = True) -> ProgenaConfig:
    """Reload configuration (useful for testing or dynamic updates).

    Args:
        project_root: Root directory of the project.
        validate: Whether to validate configuration at load time (default True).
    """
    global _config
    _config = ProgenaConfig.load(project_root, validate=validate)
    if validate:
        _log_validation_issues(_config)
    return _config
