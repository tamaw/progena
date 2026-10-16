"""Observe agent configuration."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


def _swap_database_name(url: str, db_name: str) -> str:
    """Return ``url`` with its database name replaced by ``db_name``."""
    if not url or not db_name:
        return ""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{db_name}"))


DEFAULT_PROJECT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_PROJECT_STATE_DIR = DEFAULT_PROJECT_DATA_DIR / "state"
DEFAULT_PROJECT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"
DEFAULT_PROJECT_WORKTREE_DIR = Path(__file__).resolve().parents[3] / "worktrees"
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
    """Return the managed worktree directory for the current project root."""
    if worktrees_root is not None:
        return Path(worktrees_root) / "worktrees"
    return DEFAULT_PROJECT_WORKTREE_DIR if project_root is None else project_root / "worktrees"


def _default_instructions_dir(project_root: Path | None = None) -> Path:
    """Return the checked-in prompt root unless an explicit project root is provided."""
    return _default_data_dir(project_root) / "instructions"


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
                "PROGENITOR_MODEL_DEFAULT",
                default="gpt-5-mini",
            )
            or "gpt-5-mini",
        )

    def get_model(self) -> str:
        return self.model_default


@dataclass
class StorageConfig:
    """Filesystem paths used by observe_agent."""

    data_dir: Path = field(default_factory=_default_data_dir)
    state_dir: Path = field(default_factory=_default_state_dir)
    log_dir: Path = field(default_factory=_default_log_dir)
    worktree_dir: Path = field(default_factory=_default_worktree_dir)
    instructions_dir: Path = field(default_factory=_default_instructions_dir)

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> StorageConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        data_dir = Path(os.getenv("PROGENITOR_DATA_DIR", str(_default_data_dir(project_root))))
        d_state = data_dir / "state"
        d_logs = Path(os.getenv("PROGENITOR_LOG_DIR", str(_default_log_dir(project_root))))
        d_wt = Path(
            os.getenv(
                "PROGENITOR_WORKTREE_DIR",
                str(_default_worktree_dir(project_root, worktrees_root=wt_root or None)),
            )
        )
        d_instructions = data_dir / "instructions"
        return cls(
            data_dir=data_dir,
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(d_state))),
            log_dir=d_logs,
            worktree_dir=d_wt,
            instructions_dir=Path(os.getenv("PROGENITOR_INSTRUCTIONS_DIR", str(d_instructions))),
        )


@dataclass
class DatabaseConfig:
    """Postgres settings for the observe agent's own database.

    Like the backend, the observe agent keeps its data in a separate database on
    the *same* Postgres server as the Progenitor runtime; only the database name
    differs. ``admin_url`` (the runtime database) is used to ``CREATE DATABASE``
    the observe database if it does not yet exist.
    """

    admin_url: str = ""
    database_url: str = ""
    db_name: str = "observe"
    pool_min_size: int = 1
    pool_max_size: int = 5
    setup: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        admin_url = (os.getenv("PROGENITOR_DATABASE_URL") or "").strip()
        db_name = (os.getenv("OBSERVE_DATABASE_NAME") or "observe").strip() or "observe"
        database_url = (os.getenv("OBSERVE_DATABASE_URL") or "").strip()
        if not database_url and admin_url:
            database_url = _swap_database_name(admin_url, db_name)
        setup_raw = (os.getenv("OBSERVE_DATABASE_SETUP") or "true").strip().lower()
        return cls(
            admin_url=admin_url,
            database_url=database_url,
            db_name=db_name,
            pool_min_size=_env_int("OBSERVE_DATABASE_POOL_MIN_SIZE", 1),
            pool_max_size=_env_int("OBSERVE_DATABASE_POOL_MAX_SIZE", 5),
            setup=setup_raw not in {"false", "0", "no"},
        )


@dataclass
class ObserveConfig:
    """Complete observe agent configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    log_level: str = "info"
    log_format: str = "text"
    max_tool_rounds: int = 12

    @classmethod
    def load(cls, project_root: Path | None = None) -> ObserveConfig:
        return cls(
            llm=LLMConfig.from_env(),
            storage=StorageConfig.from_env(project_root),
            database=DatabaseConfig.from_env(),
            log_level=os.getenv("PROGENITOR_LOG_LEVEL", "info"),
            log_format=os.getenv("PROGENITOR_LOG_FORMAT", "text"),
            max_tool_rounds=_env_int("OBSERVE_MAX_TOOL_ROUNDS", 12),
        )


_config: ObserveConfig | None = None


def get_config(project_root: Path | None = None) -> ObserveConfig:
    global _config
    if _config is None:
        _config = ObserveConfig.load(project_root)
    return _config


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


def setup_logging(config: ObserveConfig, agent_name: str) -> None:
    """Configure console and rotating file logging for observe_agent."""
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
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    root.setLevel(level)

    stdout_h = logging.StreamHandler(sys.stdout)
    stdout_h.setLevel(level)
    stdout_h.addFilter(_BelowErrorFilter())
    stdout_h.setFormatter(formatter)
    root.addHandler(stdout_h)

    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setLevel(logging.ERROR)
    stderr_h.setFormatter(formatter)
    root.addHandler(stderr_h)

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
