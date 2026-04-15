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


DEFAULT_PROJECT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_PROJECT_STATE_DIR = DEFAULT_PROJECT_DATA_DIR / "state"
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
DEFAULT_PROJECT_WORKTREES_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROJECT_WORKTREE_DIR = DEFAULT_PROJECT_WORKTREES_ROOT / "worktrees"


def _default_data_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in data directory unless an explicit project root is provided."""
    return DEFAULT_PROJECT_DATA_DIR if project_root is None else project_root / "data"


def _default_state_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in state directory unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "state"


def _default_log_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in logs directory unless an explicit project root is provided."""
    return DEFAULT_PROJECT_LOG_DIR if project_root is None else project_root / "logs"


def _default_worktrees_root(project_root: Path | None = None) -> Path:
    """Return the workspace root that owns the managed worktrees/ directory."""
    return DEFAULT_PROJECT_WORKTREES_ROOT if project_root is None else project_root


def _default_worktree_dir(
    project_root: Path | None = None,
    *,
    worktrees_root: Path | str | None = None,
) -> Path:
    """Return the managed worktree directory for the current workspace root."""
    root = (
        Path(worktrees_root)
        if worktrees_root is not None
        else _default_worktrees_root(project_root)
    )
    return root / "worktrees"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass
class LLMConfig:
    """Azure OpenAI Responses API settings."""

    base_url: str = ""
    api_key: str | None = None
    model_default: str = "gpt-5-mini"

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            base_url=os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            model_default=_first_env(
                "AGENT_MODEL_DEFAULT",
                default="gpt-5-mini",
            )
            or "gpt-5-mini",
        )

    def get_model(self) -> str:
        return self.model_default


@dataclass
class StorageConfig:
    """Filesystem paths."""

    data_dir: Path = field(default_factory=_default_data_dir)
    state_dir: Path = field(default_factory=_default_state_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    worktree_dir: Path = field(default_factory=_default_worktree_dir)

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> StorageConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        data_dir = Path(os.getenv("PROGENITOR_DATA_DIR", str(_default_data_dir(project_root))))
        return cls(
            data_dir=data_dir,
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(data_dir / "state"))),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", str(_default_log_dir(project_root)))),
            worktree_dir=Path(
                os.getenv(
                    "PROGENITOR_WORKTREE_DIR",
                    str(_default_worktree_dir(project_root, worktrees_root=wt_root or None)),
                )
            ),
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
    def from_env(cls, project_root: Path | None = None) -> CopilotConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        workspace_root = Path(wt_root) if wt_root else _default_worktrees_root(project_root)
        return cls(
            bin_path=os.getenv("PROGENITOR_COPILOT_BIN", "copilot"),
            timeout_seconds=_env_int("PROGENITOR_COPILOT_TIMEOUT_SECONDS", 1200),
            additional_flags=_env_list("PROGENITOR_COPILOT_ADDITIONAL_FLAGS"),
            model=os.getenv("DEFAULT_COPILOT_MODEL", "gpt-5-mini"),
            allowed_dirs=[str(workspace_root)],
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
            copilot=CopilotConfig.from_env(project_root),
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
