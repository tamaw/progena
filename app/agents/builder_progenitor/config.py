"""Builder agent configuration."""

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


def _first_env(*keys: str, default: str | None = None) -> str | None:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_list(key: str) -> list[str]:
    raw = os.getenv(key, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    """Azure OpenAI Responses API settings."""

    base_url: str = ""
    api_key: str | None = None
    model_default: str = "gpt-5-mini"
    model_planner_override: str | None = None
    model_builder_override: str | None = None

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=_first_env(
                "PROGENITOR_BUILDER_PROGENITOR_MODEL_DEFAULT",
                "PROGENITOR_COPILOT_DEFAULT",
                "PROGENITOR_MODEL_DEFAULT",
                default="gpt-5.4",
            ),
            model_planner_override=_first_env(
                "PROGENITOR_BUILDER_PROGENITOR_MODEL_PLANNER",
                "PROGENITOR_MODEL_PLANNER_OVERRIDE",
                "PROGENITOR_MODEL_PLANNER",
            ),
            model_builder_override=_first_env(
                "PROGENITOR_BUILDER_PROGENITOR_MODEL_BUILDER",
                "PROGENITOR_MODEL_BUILDER_OVERRIDE",
                "PROGENITOR_MODEL_BUILDER",
            ),
        )

    def get_model_for_role(self, role: str | None = None) -> str:
        if role == "planner" and self.model_planner_override:
            return self.model_planner_override
        if role == "builder" and self.model_builder_override:
            return self.model_builder_override
        return self.model_default


@dataclass
class StorageConfig:
    """Filesystem paths."""

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> StorageConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        if project_root:
            d_state = project_root / "data" / "state"
            d_logs = project_root / "logs"
            d_wt = Path(wt_root) / "worktrees" if wt_root else project_root / "worktrees"
        else:
            d_state = Path("/data/state")
            d_logs = Path("/logs")
            d_wt = Path(wt_root) / "worktrees" if wt_root else Path("/worktrees")
        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(d_state))),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", str(d_logs))),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", str(d_wt))),
        )


@dataclass
class CopilotConfig:
    """GitHub Copilot ACP settings."""

    bin_path: str = "copilot"
    timeout_seconds: int = 1200
    additional_flags: list[str] = field(default_factory=list)
    model: str = "gpt-5-mini"
    allowed_dirs: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> CopilotConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip() or "/worktrees"
        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", "copilot"),
            timeout_seconds=_env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", 1200),
            additional_flags=_env_list("PROGENITOR_COPILOT_ADDITIONAL_FLAGS"),
            model=_first_env(
                "PROGENITOR_BUILDER_PROGENITOR_COPILOT_MODEL",
                "PROGENITOR_COPILOT_DEFAULT",
                default="gpt-5.4",
            ),
            allowed_dirs=[wt_root],
        )


@dataclass
class BuilderConfig:
    """Complete builder configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    copilot: CopilotConfig = field(default_factory=CopilotConfig)
    log_level: str = "info"
    log_format: str = "text"

    @classmethod
    def load(cls, project_root: Path | None = None) -> BuilderConfig:
        return cls(
            llm=LLMConfig.from_env(),
            storage=StorageConfig.from_env(project_root),
            copilot=CopilotConfig.from_env(),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
        )


_config: BuilderConfig | None = None


def get_config(project_root: Path | None = None) -> BuilderConfig:
    global _config
    if _config is None:
        _config = BuilderConfig.load(project_root)
    return _config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class _BelowErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def setup_logging(config: BuilderConfig, agent_name: str) -> None:
    """Configure console + rotating file logging."""
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    formatter: logging.Formatter
    if config.log_format == "json":
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    root = logging.getLogger()
    for h in root.handlers[:]:
        h.close()
        root.removeHandler(h)
    root.setLevel(level)

    # Console: stdout < ERROR, stderr >= ERROR
    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(level)
    stdout_h.addFilter(_BelowErrorFilter())
    stdout_h.setFormatter(formatter)
    root.addHandler(stdout_h)

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(logging.ERROR)
    stderr_h.setFormatter(formatter)
    root.addHandler(stderr_h)

    # Single rotating log file
    try:
        log_dir = config.storage.log_dir / agent_name
        log_dir.mkdir(parents=True, exist_ok=True)
        file_h = logging.handlers.RotatingFileHandler(
            log_dir / f"{agent_name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_h.setLevel(level)
        file_h.setFormatter(formatter)
        root.addHandler(file_h)
    except OSError:
        pass

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
