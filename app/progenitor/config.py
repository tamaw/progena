"""
Centralized Configuration for Progenitor.

Configuration is read from environment variables and live config files.
"""

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from string import Template
from typing import Any

from dotenv import load_dotenv

from .utils import env_bool, env_float, env_int, env_list

# Auto-load .env file from project root
load_dotenv()
logger = logging.getLogger(__name__)

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


def _resolve_required_env_template(value: str) -> tuple[str | None, list[str]]:
    """Resolve $VAR/${VAR} placeholders, returning missing variable names when unresolved."""
    template = Template(value)
    missing: list[str] = []

    for match in template.pattern.finditer(value):
        var_name = match.group("named") or match.group("braced")
        if var_name is not None and os.getenv(var_name) is None:
            missing.append(var_name)

    if missing:
        return None, missing

    return template.safe_substitute(os.environ), []


def default_mcp_config_payload() -> dict[str, Any]:
    """Return the default MCP server configuration written to data/config/mcp.json."""
    return {
        "mcpServers": {
            "jina": {
                "enabled": False,
                "transport": "http",
                "description": "Jina AI web search and page read tools",
                "url": "https://mcp.jina.ai/v1?include_tags=search,read",
                "headers": {
                    "Authorization": "Bearer ${JINA_API_KEY}",
                },
            }
        }
    }


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

    # Per-role model overrides (optional)
    model_planner: str | None = None
    model_builder: str | None = None
    model_summary: str | None = None
    # Reasoning/thinking feature (for models that support it)
    reasoning_enabled: bool = True
    reasoning_budget_tokens: int | None = None
    reasoning_summary: str = "auto"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Load LLM config from environment."""
        budget_tokens_str = os.getenv("PROGENITOR_REASONING_BUDGET_TOKENS", "").strip()
        budget_tokens = int(budget_tokens_str) if budget_tokens_str else None

        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"),
            model_planner=os.getenv("PROGENITOR_MODEL_PLANNER"),
            model_builder=os.getenv("PROGENITOR_MODEL_BUILDER"),
            model_summary=os.getenv("PROGENITOR_MODEL_SUMMARY"),
            reasoning_enabled=env_bool("PROGENITOR_REASONING_ENABLED", True),
            reasoning_budget_tokens=budget_tokens,
            reasoning_summary=os.getenv("PROGENITOR_REASONING_SUMMARY", "auto").strip() or "auto",
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
            ipc_enabled=env_bool("PROGENITOR_IPC_ENABLED", fc.get("ipc_enabled", True)),
            ipc_host=os.getenv("PROGENITOR_IPC_HOST", fc.get("ipc_host", "127.0.0.1")),
            ipc_port=env_int("PROGENITOR_IPC_PORT", fc.get("ipc_port", 9500)),
            ipc_max_clients=env_int("PROGENITOR_IPC_MAX_CLIENTS", fc.get("ipc_max_clients", 10)),
            # Inner loop settings
            max_inner_iterations=env_int(
                "PROGENITOR_MAX_INNER_ITERATIONS", fc.get("max_inner_iterations", 5)
            ),
            min_verification_score=env_float(
                "PROGENITOR_MIN_VERIFICATION_SCORE", fc.get("min_verification_score", 0.8)
            ),
            iteration_time_limit_seconds=env_int(
                "PROGENITOR_ITERATION_TIME_LIMIT_SECONDS",
                fc.get("iteration_time_limit_seconds", 600),
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
            model=os.getenv("PROGENITOR_COPILOT_MODEL", "gpt-5.3-codex"),
            max_tokens=env_int("PROGENITOR_COPILOT_MAX_TOKENS", 8192),
            # ACP settings
            acp_port=env_int("PROGENITOR_COPILOT_ACP_PORT", 3000),
            acp_allowed_dirs=env_list("PROGENITOR_COPILOT_ACP_ALLOWED_DIRS", []),
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


def load_mcp_servers(
    config_path: Path | str | None = None,
) -> list[MCPServerConfig]:
    """Load external MCP server definitions from Progenitor's live config file."""
    path = Path(config_path) if config_path is not None else resolve_mcp_config_path()
    if not path.exists():
        return []

    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load MCP config from %s: %s", path, exc)
        return []

    raw_servers = raw_data.get("mcpServers") if isinstance(raw_data, dict) else None
    if not isinstance(raw_servers, dict):
        logger.warning("MCP config at %s is missing an 'mcpServers' object", path)
        return []

    servers: list[MCPServerConfig] = []
    for server_name, raw_server in raw_servers.items():
        if not isinstance(server_name, str) or not isinstance(raw_server, dict):
            continue

        transport = str(raw_server.get("transport") or "http").strip().lower()
        if transport != "http":
            logger.warning(
                "Skipping MCP server '%s': unsupported transport '%s' (remote HTTP only)",
                server_name,
                transport,
            )
            continue

        raw_url = str(raw_server.get("url") or raw_server.get("endpoint") or "").strip()
        if not raw_url:
            logger.warning("Skipping MCP server '%s': missing url", server_name)
            continue

        resolved_url, missing_url_vars = _resolve_required_env_template(raw_url)
        if resolved_url is None:
            logger.warning(
                "Skipping MCP server '%s': unresolved env vars in url: %s",
                server_name,
                ", ".join(sorted(set(missing_url_vars))),
            )
            continue

        headers: dict[str, str] = {}
        raw_headers = raw_server.get("headers")
        if isinstance(raw_headers, dict):
            for header_name, header_value in raw_headers.items():
                if not isinstance(header_name, str):
                    continue
                resolved_header, missing_header_vars = _resolve_required_env_template(
                    str(header_value)
                )
                if resolved_header is None:
                    logger.info(
                        "Skipping header '%s' for MCP server '%s': missing env vars %s",
                        header_name,
                        server_name,
                        ", ".join(sorted(set(missing_header_vars))),
                    )
                    continue
                headers[header_name] = resolved_header

        servers.append(
            MCPServerConfig(
                name=server_name,
                url=resolved_url,
                enabled=bool(raw_server.get("enabled", True)),
                transport=transport,
                headers=headers,
                description=str(raw_server.get("description") or "").strip(),
            )
        )

    return servers


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
    copilot: CopilotConfig = field(default_factory=CopilotConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)

    @classmethod
    def load(
        cls, project_root: Path | None = None, _config_dir: Path | None = None
    ) -> "ProgenitorConfig":
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
            mcp=MCPConfig.load(),
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
            logger.warning(
                "max_inner_iterations should be >= 1, got %d", self.runtime.max_inner_iterations
            )

        if self.runtime.min_verification_score < 0 or self.runtime.min_verification_score > 1:
            logger.warning(
                "min_verification_score should be between 0 and 1, got %f",
                self.runtime.min_verification_score,
            )

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
                "config_path": str(self.mcp.config_path),
                "servers": [
                    {
                        "name": server.name,
                        "url": server.url,
                        "enabled": server.enabled,
                        "transport": server.transport,
                        "description": server.description,
                        "header_keys": sorted(server.headers.keys()),
                    }
                    for server in self.mcp.servers
                ],
            },
        }

        if redact_secrets:
            result["llm"]["api_key"] = "***REDACTED***" if self.llm.api_key else None
        else:
            result["llm"]["api_key"] = self.llm.api_key

        return result


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
        (
            "max_inner_iterations",
            config.runtime.max_inner_iterations,
            lambda v: isinstance(v, int) and 0 < v <= 100,
        ),
        (
            "min_verification_score",
            config.runtime.min_verification_score,
            lambda v: isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0,
        ),
        (
            "iteration_time_limit_seconds",
            config.runtime.iteration_time_limit_seconds,
            lambda v: isinstance(v, int) and 0 < v <= 7200,
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

    if not config.copilot.bin_path:
        issues.append("Copilot Config Error: bin_path must not be empty")
    if not (0 < config.copilot.timeout_seconds <= 1800):
        issues.append(
            f"Copilot Config Error: invalid timeout_seconds={config.copilot.timeout_seconds!r}"
        )
    if not config.copilot.model:
        issues.append("Copilot Config Error: model must not be empty")
    if not (0 < config.copilot.max_tokens <= 1000000):
        issues.append(f"Copilot Config Error: invalid max_tokens={config.copilot.max_tokens!r}")

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
        _config = ProgenitorConfig.load(project_root, config_dir)
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
    _config = ProgenitorConfig.load(project_root, config_dir)
    if validate:
        _log_validation_issues(_config)
    return _config


def setup_logging(config: ProgenitorConfig, agent_name: str) -> None:
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
        for key in ("thread_id", "cycle_count", "node", "work_item_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json_module.dumps(log_entry)
