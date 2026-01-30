"""
State Reader Module.

Reads state files from data/state/ directory and provides them to the UI.
Watches for changes and notifies subscribers.

State files:
- health.json: Agent health status (status, last_active, run_id, cycle_count, pid)
- registry.json: Registered agents and tools
- current_state.json: Current execution state (messages, backlog, run_id)
- schedule.json: Scheduled tasks
- backlog.json: Task backlog
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    name: str
    kind: str
    version: str
    description: str
    capabilities: list[str]
    entrypoint: str
    endpoint: Optional[str]
    transport: str
    enabled: bool
    created_at: str
    updated_at: str
    # Runtime status fields
    status: str = "stopped"
    status_message: Optional[str] = None
    port: Optional[int] = None
    agent_card_url: Optional[str] = None
    pid: Optional[int] = None
    startup_time: Optional[str] = None
    last_heartbeat: Optional[str] = None
    last_error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "AgentInfo":
        return cls(
            name=data.get("name", "unknown"),
            kind=data.get("kind", "unknown"),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            capabilities=data.get("capabilities", []),
            entrypoint=data.get("entrypoint", ""),
            endpoint=data.get("endpoint"),
            transport=data.get("transport", "http"),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            # Runtime status
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
    """Information about a registered tool."""
    name: str
    description: str
    enabled: bool

    @classmethod
    def from_dict(cls, data: dict) -> "ToolInfo":
        return cls(
            name=data.get("name", "unknown"),
            description=data.get("description", ""),
            enabled=data.get("enabled", True),
        )


@dataclass
class HealthInfo:
    """Health information from health.json."""
    status: str
    last_active: Optional[str]
    run_id: Optional[str]
    cycle_count: int
    pid: Optional[int]

    @classmethod
    def from_dict(cls, data: dict) -> "HealthInfo":
        return cls(
            status=data.get("status", "unknown"),
            last_active=data.get("last_active"),
            run_id=data.get("run_id"),
            cycle_count=data.get("cycle_count", 0),
            pid=data.get("pid"),
        )

    def is_running(self) -> bool:
        """Check if progenitor appears to be running."""
        if self.status != "running":
            return False
        if not self.last_active:
            return False

        # Check if last_active is within last 60 seconds
        try:
            last = datetime.fromisoformat(self.last_active.replace("Z", "+00:00"))
            now = datetime.now(last.tzinfo)
            age_seconds = (now - last).total_seconds()
            return age_seconds < 60
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
    created_at: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "BacklogItem":
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
    next_run: Optional[str]
    enabled: bool

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleItem":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            schedule=data.get("schedule", ""),
            next_run=data.get("next_run"),
            enabled=data.get("enabled", True),
        )


@dataclass
class LoopInfo:
    """Information about a processing loop (Planner/Executor)."""
    name: str
    status: str  # active, idle, error
    last_activity: Optional[str] = None
    current_task: Optional[str] = None


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
    run_id: Optional[str]
    messages_count: int


class StateReader:
    """
    Reads state from data/state/ directory.

    Provides methods to get current state and watch for changes.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = state_dir or Path("data/state")
        self._cache: dict[str, tuple[float, Any]] = {}  # path -> (mtime, data)
        self._cache_ttl = 2.0  # Cache for 2 seconds
        self._last_snapshot: Optional[StateSnapshot] = None
        self._watchers: list[Callable[[StateSnapshot], None]] = []
        self._watch_task: Optional[asyncio.Task] = None

    def _read_json(self, filename: str) -> Any:
        """Read and parse a JSON file with caching.

        IMPORTANT: Opens file in read-only mode and closes handle immediately
        after reading to avoid blocking atomic renames by the progenitor agent.
        """
        filepath = self.state_dir / filename

        try:
            if not filepath.exists():
                return None

            mtime = filepath.stat().st_mtime
            cache_key = str(filepath)

            # Check cache - avoid unnecessary file reads
            if cache_key in self._cache:
                cached_mtime, cached_data = self._cache[cache_key]
                if cached_mtime == mtime:
                    return cached_data

            # Read file content into memory and close handle immediately
            # This prevents holding file locks that block atomic renames on Windows
            content = filepath.read_text(encoding="utf-8")
            data = json.loads(content)

            # Update cache
            self._cache[cache_key] = (mtime, data)
            return data

        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in %s: %s", filename, e)
            return None
        except Exception as e:
            logger.debug("Failed to read %s: %s", filename, e)
            return None

    def get_health(self) -> HealthInfo:
        """Get health information."""
        data = self._read_json("health.json")
        if data:
            return HealthInfo.from_dict(data)
        return HealthInfo(status="unknown", last_active=None, run_id=None, cycle_count=0, pid=None)

    def get_registry(self) -> tuple[list[AgentInfo], list[ToolInfo]]:
        """Get registered agents and tools."""
        data = self._read_json("registry.json")
        if not data:
            return [], []

        agents = [AgentInfo.from_dict(a) for a in data.get("agents", [])]
        tools = [ToolInfo.from_dict(t) for t in data.get("tools", [])]
        return agents, tools

    def get_backlog(self) -> list[BacklogItem]:
        """Get backlog items."""
        data = self._read_json("backlog.json")
        if not data or not isinstance(data, list):
            return []
        return [BacklogItem.from_dict(item) for item in data]

    def get_schedule(self) -> list[ScheduleItem]:
        """Get scheduled items."""
        data = self._read_json("schedule.json")
        if not data or not isinstance(data, list):
            return []
        return [ScheduleItem.from_dict(item) for item in data]

    def get_current_state(self) -> dict:
        """Get current execution state."""
        data = self._read_json("current_state.json")
        return data or {}

    def get_loops(self, health: HealthInfo) -> list[LoopInfo]:
        """Get loop status based on health and current state."""
        if not health.is_running():
            return []

        current = self.get_current_state()
        actions = current.get("actions_this_cycle", 0)

        # Determine loop states based on activity
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
        """Get a complete state snapshot."""
        health = self.get_health()
        agents, tools = self.get_registry()
        backlog = self.get_backlog()
        schedule = self.get_schedule()
        loops = self.get_loops(health)
        current = self.get_current_state()

        return StateSnapshot(
            timestamp=time.time(),
            health=health,
            agents=agents,
            tools=tools,
            backlog=backlog,
            schedule=schedule,
            loops=loops,
            cycle_count=current.get("cycle_count", 0),
            run_id=current.get("run_id"),
            messages_count=len(current.get("messages", [])),
        )

    def snapshot_to_dict(self, snapshot: StateSnapshot) -> dict:
        """Convert snapshot to JSON-serializable dict."""
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
                    "name": l.name,
                    "status": l.status,
                    "current_task": l.current_task,
                }
                for l in snapshot.loops
            ],
            "cycle_count": snapshot.cycle_count,
            "run_id": snapshot.run_id,
            "messages_count": snapshot.messages_count,
        }

    def add_watcher(self, callback: Callable[[StateSnapshot], None]):
        """Add a state change watcher."""
        self._watchers.append(callback)

    def remove_watcher(self, callback: Callable[[StateSnapshot], None]):
        """Remove a state change watcher."""
        if callback in self._watchers:
            self._watchers.remove(callback)

    async def start_watching(self, interval: float = 5.0):
        """Start watching for state changes."""
        if self._watch_task and not self._watch_task.done():
            return

        self._watch_task = asyncio.create_task(self._watch_loop(interval))

    async def stop_watching(self):
        """Stop watching for state changes."""
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

    async def _watch_loop(self, interval: float):
        """Background loop to watch for state changes."""
        while True:
            try:
                await asyncio.sleep(interval)

                snapshot = self.get_snapshot()

                # Check if state changed
                if self._state_changed(snapshot):
                    self._last_snapshot = snapshot
                    for watcher in self._watchers:
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
        """Check if state meaningfully changed."""
        if not self._last_snapshot:
            return True

        old = self._last_snapshot

        # Check key changes
        if new.health.status != old.health.status:
            return True
        if new.health.is_running() != old.health.is_running():
            return True
        if new.cycle_count != old.cycle_count:
            return True
        if len(new.agents) != len(old.agents):
            return True
        if len(new.backlog) != len(old.backlog):
            return True

        return False


# Global state reader instance
_state_reader: Optional[StateReader] = None


def get_state_reader() -> StateReader:
    """Get or create the global state reader."""
    global _state_reader
    if _state_reader is None:
        _state_reader = StateReader()
    return _state_reader
