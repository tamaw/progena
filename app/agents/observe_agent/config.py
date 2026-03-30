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

    state_dir: Path = field(default_factory=lambda: Path("/data/state"))
    log_dir: Path = field(default_factory=lambda: Path("/logs"))
    worktree_dir: Path = field(default_factory=lambda: Path("/worktrees"))
    prompts_dir: Path = field(default_factory=lambda: Path("/prompts"))

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> StorageConfig:
        wt_root = (os.getenv("PROGENITOR_WORKTREES_ROOT") or "").strip()
        if project_root:
            d_state = project_root / "data" / "state"
            d_logs = project_root / "logs"
            d_wt = Path(wt_root) / "worktrees" if wt_root else project_root / "worktrees"
            d_prompts = project_root / "prompts"
        else:
            d_state = Path("/data/state")
            d_logs = Path("/logs")
            d_wt = Path(wt_root) / "worktrees" if wt_root else Path("/worktrees")
            d_prompts = Path("/prompts")
        return cls(
            state_dir=Path(os.getenv("PROGENITOR_STATE_DIR", str(d_state))),
            log_dir=Path(os.getenv("PROGENITOR_LOG_DIR", str(d_logs))),
            worktree_dir=Path(os.getenv("PROGENITOR_WORKTREE_DIR", str(d_wt))),
            prompts_dir=Path(os.getenv("PROGENITOR_PROMPTS_DIR", str(d_prompts))),
        )


@dataclass
class ObserveConfig:
    """Complete observe agent configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    log_level: str = "info"
    log_format: str = "text"
    max_tool_rounds: int = 12

    @classmethod
    def load(cls, project_root: Path | None = None) -> ObserveConfig:
        return cls(
            llm=LLMConfig.from_env(),
            storage=StorageConfig.from_env(project_root),
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
