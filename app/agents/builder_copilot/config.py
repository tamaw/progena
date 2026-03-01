"""Local configuration for the Builder Copilot agent."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
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
    """LLM provider settings shared by the Builder Copilot runtime."""

    base_url: str = "http://localhost:4000"
    api_key: str | None = None
    model_default: str = "gpt-5.2-chat"
    model_planner: str | None = None
    model_builder: str | None = None
    model_fallbacks: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.getenv("LITELLM_BASE_URL", "http://localhost:4000"),
            api_key=os.getenv("LITELLM_API_KEY") or os.getenv("LITELLM_MASTER_KEY"),
            model_default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"),
            model_planner=os.getenv("PROGENITOR_MODEL_PLANNER"),
            model_builder=os.getenv("PROGENITOR_MODEL_BUILDER"),
            model_fallbacks=_env_list("PROGENITOR_MODEL_FALLBACKS", []),
        )

    def get_model_for_role(self, role: str | None = None) -> str:
        if role == "planner" and self.model_planner:
            return self.model_planner
        if role == "builder" and self.model_builder:
            return self.model_builder
        return self.model_default


@dataclass
class RuntimeConfig:
    """Runtime settings used by Builder Copilot."""

    log_level: str = "info"
    log_format: str = "text"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
        )


@dataclass
class StorageConfig:
    """Filesystem paths used by Builder Copilot."""

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    prompts_dir: Path = field(default_factory=lambda: Path("/prompts"))

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "StorageConfig":
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
    model: str = "claude-haiku-4.5"
    acp_allowed_dirs: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "CopilotConfig":
        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", "copilot"),
            timeout_seconds=_env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", 1200),
            default_flags=_env_list("PROGENITOR_COPILOT_DEFAULT_FLAGS", ["--yolo"]),
            model=os.getenv("PROGENITOR_COPILOT_MODEL", "claude-haiku-4.5"),
            acp_allowed_dirs=_env_list("PROGENITOR_COPILOT_ACP_ALLOWED_DIRS", []),
        )


@dataclass
class BuilderCopilotConfig:
    """Complete local config for Builder Copilot."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    copilot: CopilotConfig = field(default_factory=CopilotConfig)

    @classmethod
    def load(
        cls,
        project_root: Path | None = None,
        _config_dir: Path | None = None,
    ) -> "BuilderCopilotConfig":
        return cls(
            llm=LLMConfig.from_env(),
            runtime=RuntimeConfig.from_env(),
            storage=StorageConfig.from_env(project_root),
            copilot=CopilotConfig.from_env(),
        )


_config: BuilderCopilotConfig | None = None


def get_config(
    project_root: Path | None = None,
    config_dir: Path | None = None,
    validate: bool = True,
) -> BuilderCopilotConfig:
    del config_dir
    del validate
    global _config
    if _config is None:
        _config = BuilderCopilotConfig.load(project_root)
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


def setup_logging(config: BuilderCopilotConfig, agent_name: str = "builder_copilot") -> None:
    """Configure stdout plus a rotating file for this agent."""
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

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logging.root.handlers = [stream_handler]
    logging.root.setLevel(level)

    try:
        config.storage.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            config.storage.log_dir / f"{agent_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logging.root.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Could not create log file for %s: %s", agent_name, exc)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
