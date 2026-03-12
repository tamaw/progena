"""
State Reader Module.

Reads state/config/session files from the Progenitor data directory and log
files from the logs directory. The backend uses this as an in-memory adapter
layer; it does not own any durable UI state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolInfo:
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
            kind=data.get("kind", "mcp"),
            status=data.get("status", "ready"),
            transport=data.get("transport", "mcp"),
        )


@dataclass
class HealthInfo:
    """Health information from health.json."""

    status: str
    last_active: str | None
    run_id: str | None
    cycle_count: int
    pid: int | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthInfo:
        return cls(
            status=data.get("status", "unknown"),
            last_active=data.get("last_active"),
            run_id=data.get("run_id"),
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
            task_description=data.get("build_task_description") or data.get("task", {}).get("description"),
            started_at=data.get("build_started_at"),
            iteration=data.get("iteration", {}).get("current", 0),
            score=verification.get("current_score", verify_phase.get("score", 0.0)),
            message_count=len(data.get("build_messages", [])) + len(data.get("history", {}).get("messages", [])),
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
    run_id: str | None
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
        self.state_dir = state_dir or Path("data/state")
        self.config_dir = config_dir or self.state_dir.parent / "config"
        self.sessions_dir = sessions_dir or self.state_dir.parent / "sessions"
        self.logs_dir = logs_dir or Path("logs")
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_snapshot: StateSnapshot | None = None
        self._watchers: list[Callable[[StateSnapshot], None]] = []
        self._watch_task: asyncio.Task | None = None

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

    def _read_text_lines(self, filepath: Path, limit: int = 200) -> list[str]:
        try:
            if not filepath.exists():
                return []
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            return content.splitlines()[-limit:]
        except Exception as e:
            logger.debug("Failed to read lines from %s: %s", filepath, e)
            return []

    def get_health(self) -> HealthInfo:
        data = self._read_json("health.json")
        if data:
            return HealthInfo.from_dict(data)
        return HealthInfo(status="unknown", last_active=None, run_id=None, cycle_count=0, pid=None)

    def get_registry(self) -> tuple[list[AgentInfo], list[ToolInfo]]:
        agents_data = self._read_json_from_path(self.config_dir / "agents.json") or {}
        mcp_data = self._read_json_from_path(self.config_dir / "mcp.json") or {}

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
        tools: list[ToolInfo] = []
        if isinstance(mcp_data, dict):
            raw_tools = mcp_data.get("tools")
            if isinstance(raw_tools, list):
                tools.extend(ToolInfo.from_dict(t) for t in raw_tools if isinstance(t, dict))
            tool_map = {
                str(name): cfg
                for name, cfg in mcp_data.items()
                if name != "tools" and isinstance(cfg, dict)
            }
            seen_names = {tool.name for tool in tools}
            for name, cfg in tool_map.items():
                if name in seen_names:
                    continue
                tools.append(ToolInfo.from_dict({"name": name, **cfg}))
        return agents, tools

    def get_backlog(self) -> list[BacklogItem]:
        data = self._read_json("build_state.json")
        backlog = data.get("backlog", []) if isinstance(data, dict) else []
        if not isinstance(backlog, list):
            return []
        return [BacklogItem.from_dict(item) for item in backlog if isinstance(item, dict)]

    def get_schedule(self) -> list[ScheduleItem]:
        data = self._read_json("schedule.json")
        if not data or not isinstance(data, list):
            return []
        return [ScheduleItem.from_dict(item) for item in data if isinstance(item, dict)]

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

        latest = None
        latest_session_id = None
        for session in sessions:
            session_last = session.get("last_activity") or session.get("updated_at") or session.get("created_at")
            if session_last and (latest is None or str(session_last) > str(latest)):
                latest = str(session_last)
                latest_session_id = session.get("session_id") or session.get("id")

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

    def _normalize_widget_logs(self, entries: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
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
                current_task=f"Executing ({actions} actions)" if executor_status == "active" else None,
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
            run_id=progenitor.get("run_id"),
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

    def _line_to_entry(self, line: str, source: str, path: Path) -> dict[str, Any] | None:
        if not line.strip():
            return None

        try:
            payload = json.loads(line)
            timestamp = (
                payload.get("timestamp")
                or payload.get("time")
                or payload.get("created_at")
                or _iso_now()
            )
            message = payload.get("message") or payload.get("msg") or payload.get("content") or json.dumps(payload)
            return {
                "timestamp": str(timestamp),
                "time": str(timestamp),
                "level": str(payload.get("level", "info")).lower(),
                "source": payload.get("source") or source,
                "message": str(message),
                "path": str(path),
                "data": payload,
            }
        except json.JSONDecodeError:
            pass

        level = "info"
        lowered = line.lower()
        if "error" in lowered:
            level = "error"
        elif "warn" in lowered:
            level = "warn"
        elif "debug" in lowered:
            level = "debug"

        return {
            "timestamp": _iso_now(),
            "time": _iso_now(),
            "level": level,
            "source": source,
            "message": line.strip(),
            "path": str(path),
            "data": None,
        }

    def _path_source(self, path: Path) -> str:
        relative_parts = path.relative_to(self.logs_dir).parts if path.is_relative_to(self.logs_dir) else path.parts
        for part in reversed(relative_parts[:-1]):
            if part not in {"logs", "builder", "progenitor"}:
                return part
        return path.stem

    def get_logs(
        self,
        level: str | None = None,
        search: str | None = None,
        limit: int = 500,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        search_lc = search.lower() if search else None
        source_lc = source.lower() if source else None

        for path in self._iter_log_files():
            lines = self._read_text_lines(path, limit=min(limit, 200))
            path_source = self._path_source(path)
            for line in reversed(lines):
                entry = self._line_to_entry(line, path_source, path)
                if not entry:
                    continue
                if level and entry["level"] != level.lower():
                    continue
                if source_lc and source_lc not in str(entry["source"]).lower():
                    continue
                if search_lc and search_lc not in entry["message"].lower():
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break

        entries.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return entries[:limit]

    def get_logs_for_widget(self, widget_name: str, limit: int = 40) -> list[dict[str, Any]]:
        normalized = _normalize_name(widget_name)
        if not normalized:
            return []

        matches: list[dict[str, Any]] = []
        for path in self._iter_log_files():
            relative = str(path.relative_to(self.logs_dir)) if path.is_relative_to(self.logs_dir) else str(path)
            if normalized not in _normalize_name(relative):
                continue
            lines = self._read_text_lines(path, limit=min(limit, 80))
            source = self._path_source(path)
            for line in lines:
                entry = self._line_to_entry(line, source, path)
                if entry:
                    matches.append(entry)
        matches.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return matches[:limit]

    def get_widgets(self) -> list[dict[str, Any]]:
        health = self.get_health()
        agents, tools = self.get_registry()
        schedule = self.get_schedule()

        widgets: list[dict[str, Any]] = []
        for agent in agents:
            if not agent.enabled:
                continue
            logs = self._normalize_widget_logs(self.get_logs_for_widget(agent.name))
            status = self._status_with_health(agent.status, health.is_running())
            summary = (
                agent.status_message
                or self._summary_from_logs(logs, agent.description or f"{agent.name} is {status}")
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
                    "current_run_id": health.run_id,
                    "logs": logs,
                    "updated_at": agent.updated_at or health.last_active,
                }
            )

        for tool in tools:
            if not tool.enabled:
                continue
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
                    "current_run_id": health.run_id,
                    "logs": logs,
                    "updated_at": health.last_active,
                }
            )

        for item in schedule:
            widgets.append(
                {
                    "id": f"schedule:{item.id}",
                    "type": "schedule",
                    "name": item.name,
                    "title": item.name or item.id,
                    "description": item.raw.get("user_request") or item.name,
                    "status": "enabled" if item.enabled else "disabled",
                    "summary": item.raw.get("message") or item.name,
                    "schedule_id": item.id,
                    "schedule_type": item.raw.get("schedule_type", "cron"),
                    "expression": item.schedule,
                    "enabled": item.enabled,
                    "next_run_at": item.raw.get("next_run_at") or item.next_run,
                    "last_run_at": item.raw.get("last_run"),
                    "last_run_status": item.raw.get("last_run_status", "idle"),
                    "timezone": item.raw.get("timezone", "UTC"),
                    "logs": self._normalize_widget_logs(self.get_logs_for_widget(item.id, limit=20), limit=20),
                    "updated_at": item.raw.get("updated_at") or item.raw.get("created_at"),
                }
            )

        return widgets

    def get_workspace_payload(self) -> dict[str, Any]:
        snapshot = self.get_snapshot()
        return {
            "timestamp": snapshot.timestamp,
            "sessions": {
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
                "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
            },
            "health": {
                "status": snapshot.health.status,
                "last_active": snapshot.health.last_active,
                "run_id": snapshot.health.run_id,
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

    def snapshot_to_dict(self, snapshot: StateSnapshot) -> dict[str, Any]:
        return {
            "timestamp": snapshot.timestamp,
            "health": {
                "status": snapshot.health.status,
                "last_active": snapshot.health.last_active,
                "run_id": snapshot.health.run_id,
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
                {"name": loop_item.name, "status": loop_item.status, "current_task": loop_item.current_task}
                for loop_item in snapshot.loops
            ],
            "cycle_count": snapshot.cycle_count,
            "run_id": snapshot.run_id,
            "messages_count": snapshot.messages_count,
            "sessions": {
                "total": snapshot.sessions.total if snapshot.sessions else 0,
                "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
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
                snapshot = self.get_snapshot()
                if self._state_changed(snapshot):
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
        if len(new.agents) != len(old.agents):
            return True
        if len(new.tools) != len(old.tools):
            return True
        if len(new.schedule) != len(old.schedule):
            return True
        if len(new.backlog) != len(old.backlog):
            return True
        return (new.sessions.total if new.sessions else 0) != (old.sessions.total if old.sessions else 0)


_state_reader: StateReader | None = None


def get_state_reader() -> StateReader:
    """Get or create the global state reader."""
    global _state_reader
    if _state_reader is None:
        _state_reader = StateReader()
    return _state_reader
