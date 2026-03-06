"""Local configuration for the Builder agent."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s: %s", key, value)
        return default


def _env_list(key: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(key, "")
    if not value:
        return list(default or [])
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class LLMConfig:
    """LLM provider settings shared by the Builder runtime."""

    base_url: str = ""
    api_key: str | None = None
    model_default: str = "gpt-5.2-chat"
    model_planner: str | None = None
    model_builder: str | None = None

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"),
            model_planner=os.getenv("PROGENITOR_MODEL_PLANNER"),
            model_builder=os.getenv("PROGENITOR_MODEL_BUILDER"),
        )

    def get_model_for_role(self, role: str | None = None) -> str:
        if role == "planner" and self.model_planner:
            return self.model_planner
        if role == "builder" and self.model_builder:
            return self.model_builder
        return self.model_default


@dataclass
class RuntimeConfig:
    """Runtime settings used by Builder."""

    log_level: str = "info"
    log_format: str = "text"

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
        )


@dataclass
class StorageConfig:
    """Filesystem paths used by Builder."""

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    prompts_dir: Path = field(default_factory=lambda: Path("/prompts"))

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> StorageConfig:
        if project_root is None:
            default_state = Path("/data/state")
            default_logs = Path("/logs")
            default_worktrees = Path("/worktrees")
            default_prompts = Path("/prompts")
        else:
            default_state = project_root / "data" / "state"
            default_logs = project_root / "logs"
            default_worktrees = project_root / "worktrees"
            default_prompts = project_root / "prompts"

        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(default_state))),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", str(default_logs))),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", str(default_worktrees))),
            prompts_dir=Path(os.getenv("PROGENITOR_PROMPTS_DIR", str(default_prompts))),
        )


@dataclass
class CopilotConfig:
    """GitHub Copilot CLI settings."""

    bin_path: str = "copilot"
    timeout_seconds: int = 1200
    default_flags: list[str] = field(default_factory=lambda: ["--yolo"])
    model: str = "gpt-5.3-codex"
    acp_allowed_dirs: list[str] = field(default_factory=list)
    acp_url: str | None = None  # TCP URL for remote ACP proxy (e.g. tcp://copilot-acp:3000)

    @classmethod
    def from_env(cls) -> CopilotConfig:
        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", "copilot"),
            timeout_seconds=_env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", 1200),
            default_flags=_env_list("PROGENITOR_COPILOT_DEFAULT_FLAGS", ["--yolo"]),
            model=os.getenv("PROGENITOR_COPILOT_MODEL", "gpt-5.3-codex"),
            acp_allowed_dirs=_env_list("PROGENITOR_COPILOT_ACP_ALLOWED_DIRS", []),
            acp_url=os.getenv("COPILOT_ACP_URL"),
        )


@dataclass
class BuilderConfig:
    """Complete local config for Builder."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    copilot: CopilotConfig = field(default_factory=CopilotConfig)

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        _config_dir: Path | None = None,
    ) -> BuilderConfig:
        return cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(),
            storage=StorageConfig.from_env(project_root),
            copilot=CopilotConfig.from_env(),
        )


_config: BuilderConfig | None = None


def get_config(
    project_root: Path | None = None,
    config_dir: Path | None = None,
    validate: bool = True,
) -> BuilderConfig:
    del config_dir
    del validate
    global _config
    if _config is None:
        _config = BuilderConfig.load(project_root)
    return _config


class JsonLogFormatter(logging.Formatter):
    """Minimal JSON formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(config: BuilderConfig, agent_name: str = "builder") -> None:
    """Configure split stdout/stderr logging like progenitor runtime."""
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    level = level_map.get(config.runtime.log_level.lower(), logging.INFO)

    if config.runtime.log_format.lower() == "json":
        formatter: logging.Formatter = JsonLogFormatter()
    else:
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

    try:
        log_dir = config.storage.log_dir / agent_name
        log_dir.mkdir(parents=True, exist_ok=True)

        stdout_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{agent_name}.stdout.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stdout_file_handler.setLevel(level)
        stdout_file_handler.addFilter(_BelowErrorFilter())
        stdout_file_handler.setFormatter(formatter)
        root_logger.addHandler(stdout_file_handler)

        stderr_file_handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{agent_name}.stderr.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        stderr_file_handler.setLevel(logging.ERROR)
        stderr_file_handler.setFormatter(formatter)
        root_logger.addHandler(stderr_file_handler)
    except OSError as exc:
        logger.warning("Could not create split log files for %s: %s", agent_name, exc)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


class _BelowErrorFilter(logging.Filter):
    """Allow only records below ERROR."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR
