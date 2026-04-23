"""
State Reader Module.

Reads state/config/session files from the Progenitor data directory and log
files from the logs directory. The backend uses this as an in-memory adapter
layer; it does not own any durable UI state.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from app.progenitor.config import (
    load_mcp_servers,
    resolve_mcp_config_path,
    resolve_schedule_config_path,
)

logger = logging.getLogger(__name__)
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DEFAULT_STATE_DIR = DEFAULT_DATA_DIR / "state"
DEFAULT_LOGS_DIR = Path(__file__).resolve().parents[2] / "logs"
PLAIN_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?)"
    r"\s+-\s+(?P<logger>.*?)\s+-\s+(?P<level>[A-Z]+)\s+-\s+(?P<message>.*)$"
)
FILENAME_TIMESTAMP_RE = re.compile(r"(?P<timestamp>\d{8}_\d{6})")


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    candidate = text.replace("Z", "+00:00")
    for attempt in (candidate, candidate.replace(",", ".")):
        try:
            return datetime.fromisoformat(attempt)
        except ValueError:
            continue

    match = FILENAME_TIMESTAMP_RE.search(text)
    if match:
        try:
            return datetime.strptime(match.group("timestamp"), "%Y%m%d_%H%M%S")
        except ValueError:
            return None

    return None


def _iso_from_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat(sep=" ")
    return value.isoformat().replace("+00:00", "Z")


def _normalize_log_level(value: Any) -> str:
    level = str(value or "info").strip().lower()
    if level == "warning":
        return "warn"
    if level == "fatal":
        return "error"
    return level or "info"


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_date_boundary(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    if len(value.strip()) <= 10 and end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed


def _entry_identity(entry: dict[str, Any]) -> str:
    payload = "|".join(
        [
            str(entry.get("relative_path", "")),
            str(entry.get("timestamp", "")),
            str(entry.get("level", "")),
            str(entry.get("source", "")),
            str(entry.get("message", "")),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


@dataclass
class AgentInfo:
    """Information about a registered agent."""

    name: str
    kind: str
    version: str
    description: str
    capabilities: list[str]
    entrypoint: str
    url: str | None
    endpoint: str | None
    headers: dict[str, str] | None
    transport: str
    enabled: bool
    created_at: str
    updated_at: str
    status: str = "stopped"
    status_message: str | None = None
    port: int | None = None
    agent_card_url: str | None = None
    pid: int | None = None
    startup_time: str | None = None
    last_heartbeat: str | None = None
    last_error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentInfo:
        return cls(
            name=data.get("name", "unknown"),
            kind=data.get("kind", "unknown"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            entrypoint=data.get("entrypoint", ""),
            url=data.get("url"),
            endpoint=data.get("endpoint") or data.get("url"),
            headers=data.get("headers"),
            transport=data.get("transport", "http"),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            status=data.get("status", "stopped"),
            status_message=data.get("status_message"),
            port=data.get("port"),
            agent_card_url=data.get("agent_card_url"),
            pid=data.get("pid"),
            startup_time=data.get("startup_time"),
            last_heartbeat=data.get("last_heartbeat"),
            last_error=data.get("last_error"),
        )


@dataclass
class ToolInfo:
    """Information about a registered MCP tool."""

    name: str
    description: str
    enabled: bool
    kind: str = "mcp"
    status: str = "ready"
    transport: str = "mcp"
    url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolInfo:
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            kind=data.get("kind", "mcp"),
            status=data.get("status", "ready"),
            transport=data.get("transport", "mcp"),
            url=data.get("url"),
        )


@dataclass
class HealthInfo:
    """Health information from health.json."""

    status: str
    last_active: str | None
    thread_id: str | None
    cycle_count: int
    pid: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthInfo:
        return cls(
            status=data.get("status", "unknown"),
            last_active=data.get("last_active"),
            thread_id=data.get("thread_id"),
            cycle_count=data.get("cycle_count", 0),
            pid=data.get("pid"),
        )

    def is_running(self) -> bool:
        """Check if progenitor appears to be running."""
        if self.status != "running" or not self.last_active:
            return False
        try:
            last = datetime.fromisoformat(self.last_active.replace("Z", "+00:00"))
            now = datetime.now(last.tzinfo)
            return (now - last).total_seconds() < 60
        except Exception:
            return False


@dataclass
class BacklogItem:
    """A backlog work item."""

    id: str
    item_type: str
    status: str
    description: str
    priority: int = 0
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BacklogItem:
        return cls(
            id=data.get("id", ""),
            item_type=data.get("item_type", "task"),
            status=data.get("status", "pending"),
            description=data.get("description", ""),
            priority=data.get("priority", 0),
            created_at=data.get("created_at"),
        )


@dataclass
class ScheduleItem:
    """A scheduled task."""

    id: str
    name: str
    schedule: str
    next_run: str | None
    enabled: bool
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduleItem:
        return cls(
            id=data.get("id", ""),
            name=data.get("name") or data.get("message", ""),
            schedule=data.get("schedule") or data.get("cron_expression", ""),
            next_run=data.get("next_run") or data.get("last_run"),
            enabled=data.get("enabled", True),
            raw=data,
        )


@dataclass
class EventWidgetItem:
    """A persisted workspace event trigger."""

    id: str
    name: str
    event_type: str
    enabled: bool
    trigger_count: int
    last_triggered_at: str | None
    last_run_status: str
    summary: str
    last_payload: dict[str, Any] | None
    logs: list[dict[str, Any]]
    updated_at: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventWidgetItem:
        raw_logs = data.get("logs")
        logs = (
            [entry for entry in raw_logs if isinstance(entry, dict)]
            if isinstance(raw_logs, list)
            else []
        )
        last_payload = data.get("last_payload")
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name") or data.get("title") or data.get("event_type") or ""),
            event_type=str(data.get("event_type") or ""),
            enabled=bool(data.get("enabled", True)),
            trigger_count=_coerce_int(data.get("trigger_count"), 0),
            last_triggered_at=data.get("last_triggered_at"),
            last_run_status=str(data.get("last_run_status") or "idle"),
            summary=str(data.get("summary") or data.get("event_type") or ""),
            last_payload=last_payload if isinstance(last_payload, dict) else None,
            logs=logs,
            updated_at=data.get("updated_at") or data.get("created_at"),
            raw=data,
        )


@dataclass
class LoopInfo:
    """Information about a processing loop (Planner/Executor)."""

    name: str
    status: str
    last_activity: str | None = None
    current_task: str | None = None


@dataclass
class BuildInfo:
    """Information about current build status."""

    in_progress: bool
    phase: str | None = None
    task_description: str | None = None
    started_at: str | None = None
    iteration: int = 0
    score: float = 0.0
    message_count: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildInfo:
        verification = data.get("verification", {})
        phases = data.get("phases", {})
        verify_phase = phases.get("verify", {}) if isinstance(phases, dict) else {}
        return cls(
            in_progress=data.get("build_in_progress", False),
            phase=data.get("build_phase"),
            task_description=data.get("build_task_description")
            or data.get("task", {}).get("description"),
            started_at=data.get("build_started_at"),
            iteration=data.get("iteration", {}).get("current", 0),
            score=verification.get("current_score", verify_phase.get("score", 0.0)),
            message_count=len(data.get("build_messages", []))
            + len(data.get("history", {}).get("messages", [])),
        )


@dataclass
class SessionSummary:
    """Chat session summary."""

    total: int
    latest_session_id: str | None
    latest_activity: str | None


@dataclass
class StateSnapshot:
    """Complete state snapshot."""

    timestamp: float
    health: HealthInfo
    agents: list[AgentInfo]
    tools: list[ToolInfo]
    backlog: list[BacklogItem]
    schedule: list[ScheduleItem]
    loops: list[LoopInfo]
    cycle_count: int
    thread_id: str | None
    messages_count: int
    build: BuildInfo | None = None
    sessions: SessionSummary | None = None


class StateReader:
    """Reads state, session, config, and log data from the repo runtime dirs."""

    LOG_SUFFIXES = {".jsonl", ".log", ".txt", ".stderr", ".stdout"}

    def __init__(
        self,
        state_dir: Path | None = None,
        config_dir: Path | None = None,
        sessions_dir: Path | None = None,
        logs_dir: Path | None = None,
    ):
        data_dir = Path(os.getenv("PROGENITOR_DATA_DIR", str(DEFAULT_DATA_DIR)))
        self.state_dir = state_dir or Path(
            os.getenv("PROGENITOR_STATE_DIR", str(data_dir / "state"))
        )
        self.config_dir = config_dir or self.state_dir.parent / "config"
        self.sessions_dir = sessions_dir or self.state_dir.parent / "sessions"
        self.logs_dir = logs_dir or Path(os.getenv("PROGENITOR_LOG_DIR", str(DEFAULT_LOGS_DIR)))
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_snapshot: StateSnapshot | None = None
        self._watchers: list[Callable[[StateSnapshot], None]] = []
        self._watch_task: asyncio.Task | None = None
        self._last_watch_versions: dict[str, tuple[int, int] | None] | None = None

    @property
    def mcp_config_path(self) -> Path:
        return resolve_mcp_config_path(self.config_dir)

    @property
    def schedule_config_path(self) -> Path:
        return resolve_schedule_config_path(self.config_dir)

    def _resolved_logs_dir(self) -> Path:
        return self.logs_dir.resolve()

    def invalidate(self, *paths: Path) -> None:
        """Invalidate cached file contents."""
        if not paths:
            self._cache.clear()
            return
        for path in paths:
            self._cache.pop(str(Path(path)), None)

    def _read_json(self, filename: str) -> Any:
        return self._read_json_from_path(self.state_dir / filename)

    def _read_json_from_path(self, filepath: Path) -> Any:
        filepath = Path(filepath)
        try:
            if not filepath.exists():
                return None

            mtime = filepath.stat().st_mtime
            cache_key = str(filepath)
            if cache_key in self._cache:
                cached_mtime, cached_data = self._cache[cache_key]
                if cached_mtime == mtime:
                    return cached_data

            content = filepath.read_text(encoding="utf-8")
            data = json.loads(content)
            self._cache[cache_key] = (mtime, data)
            return data
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in %s: %s", filepath, e)
            return None
        except Exception as e:
            logger.debug("Failed to read %s: %s", filepath, e)
            return None

    def _read_text_lines(self, filepath: Path, limit: int | None = 200) -> list[str]:
        try:
            if not filepath.exists():
                return []
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            lines = content.splitlines()
            if limit is None or limit <= 0:
                return lines
            return lines[-limit:]
        except Exception as e:
            logger.debug("Failed to read lines from %s: %s", filepath, e)
            return []

    def get_health(self) -> HealthInfo:
        data = self._read_json("health.json")
        if data:
            return HealthInfo.from_dict(data)
        return HealthInfo(
            status="unknown", last_active=None, thread_id=None, cycle_count=0, pid=None
        )

    def get_registry(self) -> tuple[list[AgentInfo], list[ToolInfo]]:
        agents_data = self._read_json_from_path(self.config_dir / "agents.json") or {}

        agent_entries: list[dict[str, Any]] = []
        if isinstance(agents_data, dict):
            raw_agents = agents_data.get("agents")
            if isinstance(raw_agents, list):
                agent_entries = [a for a in raw_agents if isinstance(a, dict)]
            agent_map = {
                str(name): cfg
                for name, cfg in agents_data.items()
                if name != "agents" and isinstance(cfg, dict)
            }
            if agent_map:
                filtered = []
                seen_names = set(agent_map)
                for agent in agent_entries:
                    name = str(agent.get("name") or "")
                    if name and name in seen_names:
                        continue
                    filtered.append(agent)
                agent_entries = filtered
                for name, cfg in agent_map.items():
                    agent_entries.append({"name": name, **cfg})

        agents = [AgentInfo.from_dict(a) for a in agent_entries]
        tools = [
            ToolInfo.from_dict(
                {
                    "name": server.name,
                    "description": server.description or f"Remote MCP server at {server.url}",
                    "enabled": server.enabled,
                    "kind": "mcp_server",
                    "status": "stopped" if server.enabled else "disabled",
                    "transport": server.transport,
                    "url": server.url,
                }
            )
            for server in load_mcp_servers(self.mcp_config_path)
        ]
        return agents, tools

    def get_backlog(self) -> list[BacklogItem]:
        data = self._read_json("build_state.json")
        backlog = data.get("backlog", []) if isinstance(data, dict) else []
        if not isinstance(backlog, list):
            return []
        return [BacklogItem.from_dict(item) for item in backlog if isinstance(item, dict)]

    def get_schedule(self) -> list[ScheduleItem]:
        data = self._read_json_from_path(self.schedule_config_path)
        if data is None and not self.schedule_config_path.exists():
            data = self._read_json("schedule.json")
        if not data or not isinstance(data, list):
            return []
        return [ScheduleItem.from_dict(item) for item in data if isinstance(item, dict)]

    def get_event_widgets(self) -> list[EventWidgetItem]:
        data = self._read_json_from_path(self.config_dir / "event_widgets.json")
        if not data or not isinstance(data, list):
            return []
        return [EventWidgetItem.from_dict(item) for item in data if isinstance(item, dict)]

    def get_build_state(self) -> dict[str, Any]:
        data = self._read_json("build_state.json")
        return data or {}

    def get_progenitor_state(self) -> dict[str, Any]:
        data = self._read_json("current_state.json")
        return data or {}

    def get_sessions_summary(self) -> SessionSummary:
        data = self._read_json_from_path(self.sessions_dir / "sessions.json") or []
        sessions: list[dict[str, Any]] = []
        if isinstance(data, list):
            sessions = [entry for entry in data if isinstance(entry, dict)]
        elif isinstance(data, dict):
            raw_sessions = data.get("sessions")
            if isinstance(raw_sessions, list):
                sessions = [entry for entry in raw_sessions if isinstance(entry, dict)]
            else:
                sessions = [entry for entry in data.values() if isinstance(entry, dict)]

        latest = None
        latest_session_id = None
        latest_sort_key: tuple[int, str] | None = None
        for session in sessions:
            session_last = (
                session.get("last_activity")
                or session.get("updated_at")
                or session.get("created_at")
            )
            sort_key = self._sort_key_for_timestamp(session_last)
            if session_last and (latest_sort_key is None or sort_key > latest_sort_key):
                latest = str(session_last)
                latest_session_id = session.get("session_id") or session.get("id")
                latest_sort_key = sort_key

        return SessionSummary(
            total=len(sessions),
            latest_session_id=latest_session_id,
            latest_activity=latest,
        )

    def _status_with_health(self, status: str | None, is_running: bool) -> str:
        candidate = (status or "").strip().lower()
        if candidate:
            if candidate == "stopped" and is_running:
                return "running"
            return candidate
        return "running" if is_running else "offline"

    def _summary_from_logs(self, logs: list[dict[str, Any]], fallback: str) -> str:
        if logs:
            return str(logs[0].get("message") or fallback)
        return fallback

    def _normalize_widget_logs(
        self, entries: list[dict[str, Any]], limit: int = 20
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for entry in entries[:limit]:
            normalized.append(
                {
                    "time": entry.get("time") or entry.get("timestamp"),
                    "timestamp": entry.get("timestamp") or entry.get("time"),
                    "level": entry.get("level", "info"),
                    "source": entry.get("source") or entry.get("path") or "system",
                    "message": entry.get("message") or "",
                    "path": entry.get("path"),
                }
            )
        return normalized

    def get_loops(self, health: HealthInfo) -> list[LoopInfo]:
        if not health.is_running():
            return []

        current = self.get_progenitor_state()
        build = self.get_build_state()
        actions = current.get("actions_this_cycle", 0)
        build_actions = build.get("actions_this_cycle", 0) if isinstance(build, dict) else 0
        actions = max(actions, build_actions)

        planner_status = "active" if actions == 0 else "idle"
        executor_status = "active" if actions > 0 else "idle"
        return [
            LoopInfo(
                name="Planner",
                status=planner_status,
                last_activity=health.last_active,
                current_task="Planning next action" if planner_status == "active" else None,
            ),
            LoopInfo(
                name="Executor",
                status=executor_status,
                last_activity=health.last_active,
                current_task=f"Executing ({actions} actions)"
                if executor_status == "active"
                else None,
            ),
        ]

    def get_snapshot(self) -> StateSnapshot:
        health = self.get_health()
        agents, tools = self.get_registry()
        backlog = self.get_backlog()
        schedule = self.get_schedule()
        loops = self.get_loops(health)
        progenitor = self.get_progenitor_state()
        build = self.get_build_state()
        return StateSnapshot(
            timestamp=time.time(),
            health=health,
            agents=agents,
            tools=tools,
            backlog=backlog,
            schedule=schedule,
            loops=loops,
            cycle_count=progenitor.get("cycle_count", 0),
            thread_id=progenitor.get("thread_id"),
            messages_count=len(progenitor.get("messages", [])),
            build=BuildInfo.from_dict(build),
            sessions=self.get_sessions_summary(),
        )

    def _iter_log_files(self) -> list[Path]:
        if not self.logs_dir.exists():
            return []
        files = [path for path in self.logs_dir.rglob("*") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        return files

    def _relative_log_path(self, path: Path) -> str:
        resolved_logs_dir = self._resolved_logs_dir()
        resolved_path = path.resolve()
        if resolved_path.is_relative_to(resolved_logs_dir):
            return str(resolved_path.relative_to(resolved_logs_dir)).replace("\\", "/")
        return str(path).replace("\\", "/")

    def _log_group(self, path: Path) -> str:
        relative = Path(self._relative_log_path(path))
        parent = relative.parent
        return parent.as_posix() if parent != Path(".") else "root"

    def _path_fallback_timestamp(self, path: Path) -> str:
        relative_path = self._relative_log_path(path)
        parsed = _parse_timestamp(relative_path)
        if parsed is not None:
            return _iso_from_datetime(parsed)
        return ""

    def _sort_key_for_timestamp(self, value: Any) -> tuple[int, str]:
        parsed = _parse_timestamp(value)
        if parsed is None:
            return (0, str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return (1, parsed.astimezone(UTC).isoformat())

    def _entry_matches_source(self, entry: dict[str, Any], source: str) -> bool:
        relative_path = str(entry.get("relative_path", "")).lower()
        group = str(entry.get("group", "")).lower()
        entry_source = str(entry.get("source", "")).lower()
        return (
            source in (relative_path, group)
            or (source != "root" and relative_path.startswith(f"{source}/"))
            or source in entry_source
        )

    def get_log_sources(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}

        for path in self._iter_log_files():
            group = self._log_group(path)
            relative_path = self._relative_log_path(path)
            grouped.setdefault(group, []).append(
                {
                    "id": relative_path,
                    "label": path.name,
                    "path": relative_path,
                    "group": group,
                    "source": self._path_source(path),
                    "updated_at": self._path_fallback_timestamp(path),
                }
            )

        source_groups: list[dict[str, Any]] = []
        for group, options in sorted(grouped.items(), key=lambda item: item[0].lower()):
            options.sort(key=lambda item: item["label"].lower())
            source_groups.append(
                {
                    "id": group,
                    "label": group,
                    "options": options,
                }
            )

        return source_groups

    def get_log_folders(self) -> list[dict[str, Any]]:
        folders = []
        for group in self.get_log_sources():
            folders.append(
                {
                    "id": group["id"],
                    "label": group["label"],
                    "file_count": len(group.get("options", [])),
                }
            )
        return folders

    def _line_to_entry(self, line: str, source: str, path: Path) -> dict[str, Any] | None:
        if not line.strip():
            return None

        relative_path = self._relative_log_path(path)
        group = self._log_group(path)

        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                fallback_timestamp = self._path_fallback_timestamp(path)
                entry = {
                    "timestamp": fallback_timestamp,
                    "time": fallback_timestamp,
                    "level": "info",
                    "source": source,
                    "message": str(payload),
                    "path": str(path),
                    "relative_path": relative_path,
                    "group": group,
                    "data": payload,
                }
                entry["id"] = _entry_identity(entry)
                return entry
            timestamp = (
                payload.get("timestamp")
                or payload.get("time")
                or payload.get("created_at")
                or self._path_fallback_timestamp(path)
            )
            message = (
                payload.get("message")
                or payload.get("msg")
                or payload.get("content")
                or json.dumps(payload)
            )
            entry = {
                "timestamp": str(timestamp),
                "time": str(timestamp),
                "level": _normalize_log_level(payload.get("level", "info")),
                "source": payload.get("source") or source,
                "message": str(message),
                "path": str(path),
                "relative_path": relative_path,
                "group": group,
                "data": payload,
            }
            entry["id"] = _entry_identity(entry)
            return entry
        except json.JSONDecodeError:
            pass

        matched = PLAIN_LOG_LINE_RE.match(line.strip())
        if matched:
            timestamp = matched.group("timestamp")
            entry = {
                "timestamp": timestamp,
                "time": timestamp,
                "level": _normalize_log_level(matched.group("level")),
                "source": matched.group("logger") or source,
                "message": matched.group("message").strip(),
                "path": str(path),
                "relative_path": relative_path,
                "group": group,
                "data": None,
            }
            entry["id"] = _entry_identity(entry)
            return entry

        level = "info"
        lowered = line.lower()
        if "error" in lowered:
            level = "error"
        elif "warn" in lowered:
            level = "warn"
        elif "debug" in lowered:
            level = "debug"

        fallback_timestamp = self._path_fallback_timestamp(path)
        entry = {
            "timestamp": fallback_timestamp,
            "time": fallback_timestamp,
            "level": level,
            "source": source,
            "message": line.strip(),
            "path": str(path),
            "relative_path": relative_path,
            "group": group,
            "data": None,
        }
        entry["id"] = _entry_identity(entry)
        return entry

    def _path_source(self, path: Path) -> str:
        resolved_logs_dir = self._resolved_logs_dir()
        resolved_path = path.resolve()
        relative_parts = (
            resolved_path.relative_to(resolved_logs_dir).parts
            if resolved_path.is_relative_to(resolved_logs_dir)
            else resolved_path.parts
        )
        for part in reversed(relative_parts[:-1]):
            if part not in {"logs", "builder", "progenitor"}:
                return part
        return path.stem

    def _entry_search_text(self, entry: dict[str, Any]) -> str:
        fields = [
            entry.get("message", ""),
            entry.get("source", ""),
            entry.get("relative_path", ""),
            entry.get("group", ""),
            entry.get("level", ""),
        ]
        return " ".join(str(field) for field in fields if field).lower()

    def _candidate_log_files(
        self,
        *,
        source: str | None = None,
        folder: str | None = None,
        file_path: str | None = None,
    ) -> list[Path]:
        if not self.logs_dir.exists():
            return []

        resolved_logs_dir = self._resolved_logs_dir()

        if file_path:
            candidate = (resolved_logs_dir / file_path).resolve()
            with contextlib.suppress(ValueError):
                candidate.relative_to(resolved_logs_dir)
                if candidate.exists() and candidate.is_file():
                    return [candidate]
            return []

        if folder:
            folder_path = (resolved_logs_dir / folder).resolve()
            with contextlib.suppress(ValueError):
                folder_path.relative_to(resolved_logs_dir)
                if folder_path.exists():
                    if folder_path.is_file():
                        return [folder_path]
                    files = [path for path in folder_path.rglob("*") if path.is_file()]
                    files.sort(
                        key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True
                    )
                    return files
            return []

        files = self._iter_log_files()
        if not source:
            return files

        source_lc = source.lower()
        return [
            path
            for path in files
            if self._entry_matches_source(
                {
                    "relative_path": self._relative_log_path(path),
                    "group": self._log_group(path),
                    "source": self._path_source(path),
                },
                source_lc,
            )
        ]

    def _filter_log_entries(
        self,
        *,
        level: str | None = None,
        search: str | None = None,
        source: str | None = None,
        folder: str | None = None,
        file_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search_mode: str = "plain",
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        search_lc = search.lower() if search else None
        source_lc = source.lower() if source else None
        folder_lc = folder.lower() if folder else None
        file_path_lc = file_path.lower() if file_path else None
        regex = None
        start_boundary = _parse_date_boundary(start_date, end_of_day=False)
        end_boundary = _parse_date_boundary(end_date, end_of_day=True)

        if search and search_mode == "regex":
            regex = re.compile(search, re.IGNORECASE)

        candidate_files = self._candidate_log_files(
            source=source, folder=folder, file_path=file_path
        )

        for path in candidate_files:
            lines = self._read_text_lines(path, limit=None)
            path_source = self._path_source(path)
            for line in reversed(lines):
                entry = self._line_to_entry(line, path_source, path)
                if not entry:
                    continue
                entry_timestamp = _parse_timestamp(entry.get("timestamp"))
                if start_boundary or end_boundary:
                    if entry_timestamp is None:
                        continue
                    if entry_timestamp.tzinfo is None:
                        entry_timestamp = entry_timestamp.replace(tzinfo=UTC)
                    else:
                        entry_timestamp = entry_timestamp.astimezone(UTC)
                    if start_boundary and entry_timestamp < start_boundary:
                        break
                    if end_boundary and entry_timestamp > end_boundary:
                        continue
                if level and entry["level"] != level.lower():
                    continue
                if folder_lc and entry["group"].lower() != folder_lc:
                    continue
                if file_path_lc and entry["relative_path"].lower() != file_path_lc:
                    continue
                if source_lc and not self._entry_matches_source(entry, source_lc):
                    continue
                if search:
                    searchable = self._entry_search_text(entry)
                    if regex and not regex.search(searchable):
                        continue
                    if not regex and search_lc and search_lc not in searchable:
                        continue
                entries.append(entry)

        entries.sort(
            key=lambda item: self._sort_key_for_timestamp(item.get("timestamp")), reverse=True
        )
        return entries

    def query_logs(
        self,
        *,
        level: str | None = None,
        search: str | None = None,
        limit: int = 500,
        source: str | None = None,
        folder: str | None = None,
        file_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        after: str | None = None,
        search_mode: str = "plain",
    ) -> dict[str, Any]:
        entries = self._filter_log_entries(
            level=level,
            search=search,
            source=source,
            folder=folder,
            file_path=file_path,
            start_date=start_date,
            end_date=end_date,
            search_mode=search_mode,
        )
        newest_cursor = entries[0]["id"] if entries else ""
        cursor_found = True
        returned_entries = entries

        if after:
            cursor_index = next(
                (index for index, entry in enumerate(entries) if entry.get("id") == after), None
            )
            if cursor_index is None:
                cursor_found = False
            else:
                returned_entries = entries[:cursor_index]

        if limit < 0:
            returned_entries = []
        elif limit > 0:
            returned_entries = returned_entries[:limit]

        return {
            "entries": returned_entries,
            "total": len(entries),
            "returned": len(returned_entries),
            "newest_cursor": newest_cursor,
            "cursor_found": cursor_found,
        }

    def get_logs(
        self,
        level: str | None = None,
        search: str | None = None,
        limit: int = 500,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        logs = self.query_logs(level=level, search=search, limit=limit, source=source)
        return cast(list[dict[str, Any]], logs["entries"])

    def get_logs_for_widget(self, widget_name: str, limit: int = 40) -> list[dict[str, Any]]:
        normalized = _normalize_name(widget_name)
        if not normalized:
            return []

        matches: list[dict[str, Any]] = []
        for path in self._iter_log_files():
            relative = self._relative_log_path(path)
            if normalized not in _normalize_name(relative):
                continue
            lines = self._read_text_lines(path, limit=limit)
            source = self._path_source(path)
            for line in lines:
                entry = self._line_to_entry(line, source, path)
                if entry:
                    matches.append(entry)
        matches.sort(
            key=lambda item: self._sort_key_for_timestamp(item.get("timestamp")), reverse=True
        )
        return matches[:limit]

    def get_widgets(self) -> list[dict[str, Any]]:
        health = self.get_health()
        agents, tools = self.get_registry()
        schedule = self.get_schedule()
        event_widgets = self.get_event_widgets()

        widgets: list[dict[str, Any]] = []
        for agent in agents:
            if not agent.enabled:
                continue
            logs = self._normalize_widget_logs(self.get_logs_for_widget(agent.name))
            status = self._status_with_health(agent.status, health.is_running())
            summary = agent.status_message or self._summary_from_logs(
                logs, agent.description or f"{agent.name} is {status}"
            )
            widgets.append(
                {
                    "id": f"agent:{agent.name}",
                    "type": "agent",
                    "name": agent.name,
                    "title": agent.name,
                    "description": agent.description,
                    "kind": agent.kind,
                    "status": status,
                    "summary": summary,
                    "transport": agent.transport,
                    "current_thread_id": health.thread_id,
                    "logs": logs,
                    "updated_at": agent.updated_at or health.last_active,
                }
            )

        for tool in tools:
            logs = self._normalize_widget_logs(self.get_logs_for_widget(tool.name))
            summary = self._summary_from_logs(logs, tool.description or f"MCP {tool.name}")
            widgets.append(
                {
                    "id": f"mcp:{tool.name}",
                    "type": "mcp",
                    "name": tool.name,
                    "title": tool.name,
                    "description": tool.description,
                    "kind": tool.kind,
                    "status": self._status_with_health(tool.status, health.is_running()),
                    "summary": summary,
                    "transport": tool.transport,
                    "enabled": tool.enabled,
                    "url": tool.url,
                    "current_thread_id": health.thread_id,
                    "logs": logs,
                    "updated_at": health.last_active,
                }
            )

        for schedule_item in schedule:
            widgets.append(
                {
                    "id": f"schedule:{schedule_item.id}",
                    "type": "schedule",
                    "name": schedule_item.name,
                    "title": schedule_item.name or schedule_item.id,
                    "description": schedule_item.raw.get("user_request") or schedule_item.name,
                    "status": "enabled" if schedule_item.enabled else "disabled",
                    "summary": schedule_item.raw.get("message") or schedule_item.name,
                    "instruction": schedule_item.raw.get("instruction")
                    or schedule_item.raw.get("message")
                    or "",
                    "user_request": schedule_item.raw.get("user_request") or "",
                    "schedule_id": schedule_item.id,
                    "schedule_type": schedule_item.raw.get("schedule_type", "cron"),
                    "expression": schedule_item.schedule,
                    "enabled": schedule_item.enabled,
                    "next_run_at": schedule_item.raw.get("next_run_at") or schedule_item.next_run,
                    "last_run_at": schedule_item.raw.get("last_run"),
                    "last_run_status": schedule_item.raw.get("last_run_status", "idle"),
                    "timezone": schedule_item.raw.get("timezone", "UTC"),
                    "logs": self._normalize_widget_logs(
                        self.get_logs_for_widget(schedule_item.id, limit=20), limit=20
                    ),
                    "updated_at": schedule_item.raw.get("updated_at")
                    or schedule_item.raw.get("created_at"),
                }
            )

        for event_widget in event_widgets:
            widgets.append(
                {
                    "id": f"event:{event_widget.id}",
                    "type": "event",
                    "name": event_widget.name,
                    "title": event_widget.name or event_widget.event_type,
                    "description": event_widget.raw.get("description") or event_widget.event_type,
                    "status": "enabled" if event_widget.enabled else "disabled",
                    "summary": event_widget.summary or event_widget.event_type,
                    "event_type": event_widget.event_type,
                    "enabled": event_widget.enabled,
                    "trigger_count": event_widget.trigger_count,
                    "last_triggered_at": event_widget.last_triggered_at,
                    "last_run_status": event_widget.last_run_status,
                    "last_payload": event_widget.last_payload,
                    "logs": self._normalize_widget_logs(event_widget.logs, limit=20),
                    "updated_at": event_widget.updated_at,
                }
            )

        return widgets

    def get_workspace_payload(self) -> dict[str, Any]:
        snapshot = self.get_snapshot()
        return {
            "timestamp": snapshot.timestamp,
            "sessions": {
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id
                if snapshot.sessions
                else None,
                "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
            },
            "health": {
                "status": snapshot.health.status,
                "last_active": snapshot.health.last_active,
                "thread_id": snapshot.health.thread_id,
                "cycle_count": snapshot.health.cycle_count,
                "pid": snapshot.health.pid,
                "is_running": snapshot.health.is_running(),
            },
            "widgets": self.get_widgets(),
            "workspace": {
                "storage": "indexeddb",
                "layout_owner": "browser",
            },
        }

    def _watched_paths(self) -> list[Path]:
        return [
            self.state_dir / "health.json",
            self.state_dir / "current_state.json",
            self.state_dir / "build_state.json",
            self.schedule_config_path,
            self.config_dir / "agents.json",
            self.config_dir / "mcp.json",
            self.config_dir / "event_widgets.json",
            self.sessions_dir / "sessions.json",
        ]

    def _path_version(self, path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def _collect_watch_versions(self) -> dict[str, tuple[int, int] | None]:
        return {str(path): self._path_version(path) for path in self._watched_paths()}

    def snapshot_to_dict(self, snapshot: StateSnapshot) -> dict[str, Any]:
        return {
            "timestamp": snapshot.timestamp,
            "health": {
                "status": snapshot.health.status,
                "last_active": snapshot.health.last_active,
                "thread_id": snapshot.health.thread_id,
                "cycle_count": snapshot.health.cycle_count,
                "pid": snapshot.health.pid,
                "is_running": snapshot.health.is_running(),
            },
            "agents": [
                {
                    "name": a.name,
                    "kind": a.kind,
                    "version": a.version,
                    "description": a.description,
                    "enabled": a.enabled,
                    "transport": a.transport,
                }
                for a in snapshot.agents
            ],
            "tools": [
                {"name": t.name, "description": t.description, "enabled": t.enabled}
                for t in snapshot.tools
            ],
            "backlog": [
                {
                    "id": b.id,
                    "type": b.item_type,
                    "status": b.status,
                    "description": b.description,
                    "priority": b.priority,
                }
                for b in snapshot.backlog
            ],
            "schedule": [
                {
                    "id": s.id,
                    "name": s.name,
                    "schedule": s.schedule,
                    "next_run": s.next_run,
                    "enabled": s.enabled,
                }
                for s in snapshot.schedule
            ],
            "loops": [
                {
                    "name": loop_item.name,
                    "status": loop_item.status,
                    "current_task": loop_item.current_task,
                }
                for loop_item in snapshot.loops
            ],
            "cycle_count": snapshot.cycle_count,
            "thread_id": snapshot.thread_id,
            "messages_count": snapshot.messages_count,
            "sessions": {
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id
                if snapshot.sessions
                else None,
                "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
            },
            "widgets": self.get_widgets(),
        }

    def add_watcher(self, callback: Callable[[StateSnapshot], None]) -> None:
        self._watchers.append(callback)

    def remove_watcher(self, callback: Callable[[StateSnapshot], None]) -> None:
        if callback in self._watchers:
            self._watchers.remove(callback)

    async def start_watching(self, interval: float = 5.0) -> None:
        if self._watch_task and not self._watch_task.done():
            return
        self._watch_task = asyncio.create_task(self._watch_loop(interval))

    async def stop_watching(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch_task
            self._watch_task = None

    async def _watch_loop(self, interval: float) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                versions = self._collect_watch_versions()
                if self._last_watch_versions is None:
                    self._last_watch_versions = versions
                    self._last_snapshot = self.get_snapshot()
                    continue

                changed_paths = [
                    Path(path)
                    for path, version in versions.items()
                    if self._last_watch_versions.get(path) != version
                ]
                if not changed_paths:
                    continue

                self._last_watch_versions = versions
                self.invalidate(*changed_paths)
                snapshot = self.get_snapshot()
                self._last_snapshot = snapshot
                for watcher in list(self._watchers):
                    try:
                        watcher(snapshot)
                    except Exception as e:
                        logger.warning("Watcher error: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Watch loop error: %s", e)
                await asyncio.sleep(interval)

    def _state_changed(self, new: StateSnapshot) -> bool:
        if not self._last_snapshot:
            return True

        old = self._last_snapshot
        if new.health.status != old.health.status:
            return True
        if new.health.is_running() != old.health.is_running():
            return True
        if new.cycle_count != old.cycle_count:
            return True
        if new.thread_id != old.thread_id:
            return True
        if new.messages_count != old.messages_count:
            return True
        if len(new.agents) != len(old.agents):
            return True
        if len(new.tools) != len(old.tools):
            return True
        if len(new.schedule) != len(old.schedule):
            return True
        if len(new.backlog) != len(old.backlog):
            return True
        return (new.sessions.total if new.sessions else 0) != (
            old.sessions.total if old.sessions else 0
        )


_state_reader: StateReader | None = None


def get_state_reader() -> StateReader:
    """Get or create the global state reader."""
    global _state_reader
    if _state_reader is None:
        _state_reader = StateReader()
    return _state_reader
