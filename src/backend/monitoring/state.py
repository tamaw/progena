"""
State Reader Module.

Reads state documents from the shared Postgres document store and operational
logs from the configured log directory. The backend uses this as a read/write
adapter layer; Progena remains the owner of runtime state semantics.

The virtual `/config/*.json` names are database-backed views, not files under the
repository's data directory.

"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import src.backend.lifecycle.state as lifecycle_state
from src.backend.logging_setup import get_log_dir
from src.backend.storage.documents import (
    BACKEND_STATE_PREFIX,
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    StoreChangeListener,
    get_default_document_store,
    store_database_url,
)
from src.progena.config import default_mcp_config_payload
from src.progena.documents.content import AGENT_DOCUMENT_KEY_PREFIX, SKILL_DOCUMENT_KEY_PREFIX
from src.progena.documents.kinds import (
    ACP_TASK_KEY_PREFIX,
    CANVAS_DATA_KEY_PREFIX,
    CANVAS_DOCUMENT_KEY_PREFIX,
    CANVAS_STATUS_KEY_PREFIX,
    INBOX_MESSAGE_KEY_PREFIX,
    KNOWLEDGE_PAGE_KEY_PREFIX,
    REPO_KEY_PREFIX,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from src.progena.knowledge.indexer_config import INDEX_STATUS_DOC_KEY
from src.progena.mcp.loader import (
    normalize_mcp_config_payload,
    resolve_required_env_template,
)
from src.progena.routines.store import ROUTINE_GRAPH_KEY_PREFIX
from src.progena.settings.model_profiles import MODEL_PROFILES_DOC_KEY

logger = logging.getLogger(__name__)
PLAIN_LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?)"
    r"\s+-\s+(?P<logger>.*?)\s+-\s+(?P<level>[A-Z]+)\s+-\s+(?P<message>.*)$"
)
FILENAME_TIMESTAMP_RE = re.compile(r"(?P<timestamp>\d{8}_\d{6})")


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
    # Log lines carry no zone; both services write them in UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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


def _live_progena_reachable() -> bool | None:
    """Live liveness check via the backend's SSE heartbeat, or None if unavailable.

    Reads the shared lifecycle state. Returns None whenever there's no SSE
    client actually configured yet - not just "unreachable" - which is the
    case in most tests and before backend startup wires one up. Only once a
    client exists do we report a definite True/False, so callers correctly
    fall back to the timestamp-staleness heuristic instead of every
    unconfigured test reporting a false "down".
    """
    try:
        client = getattr(lifecycle_state, "client", None)
        if client is None:
            return None
        return bool(client.is_running)
    except Exception:
        return None


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


def _hash_material(material: Any) -> str:
    """Stable short hash of arbitrary JSON-ish content, for change-detection fingerprints.

    Shared tail for every ``_*_fingerprint`` method on ``StateReader``: each
    gathers its own document(s) into ``material``, then calls this to turn it
    into the sha1 prefix ``_state_changed`` compares between polls.
    """
    try:
        serialized = json.dumps(material, sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialized = str(material)
    return hashlib.sha1(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


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
    agent_card: dict[str, Any] | None = None
    pid: int | None = None
    startup_time: str | None = None
    last_heartbeat: str | None = None
    last_error: str | None = None
    acp: dict[str, Any] | None = None

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
            agent_card=data.get("agent_card") if isinstance(data.get("agent_card"), dict) else None,
            pid=data.get("pid"),
            startup_time=data.get("startup_time"),
            last_heartbeat=data.get("last_heartbeat"),
            last_error=data.get("last_error"),
            acp=data.get("acp") if isinstance(data.get("acp"), dict) else None,
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
    """Health information from the ``health`` Postgres document."""

    status: str
    last_active: str | None
    thread_id: str | None
    cycle_count: int
    pid: int | None
    # Set only by get_health(), from a live SSE heartbeat check - not part of
    # the stored document. None means "no live signal available" (e.g. built
    # directly in a test), in which case is_running() falls back to the
    # timestamp-staleness heuristic below.
    live_reachable: bool | None = None
    # Short git commit SHA the runtime process was started from, and when it
    # started - lets you spot a stale-process-running-old-code mismatch (the
    # exact bug class this repo hit more than once) without DB archaeology.
    version: str | None = None
    started_at: str | None = None
    llm_model: str | None = None
    llm_reasoning_enabled: bool | None = None
    llm_reasoning_effort: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HealthInfo:
        return cls(
            status=data.get("status", "unknown"),
            last_active=data.get("last_active"),
            thread_id=data.get("thread_id"),
            cycle_count=data.get("cycle_count", 0),
            pid=data.get("pid"),
            version=data.get("version"),
            started_at=data.get("started_at"),
            llm_model=data.get("llm_model"),
            llm_reasoning_enabled=data.get("llm_reasoning_enabled"),
            llm_reasoning_effort=data.get("llm_reasoning_effort"),
        )

    def is_running(self) -> bool:
        """Check if progena appears to be running."""
        if self.live_reachable is not None:
            return self.live_reachable
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
            schedule=data.get("schedule")
            or data.get("cron_expression")
            or data.get("scheduled_at")
            or "",
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
    updated_at: str | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventWidgetItem:
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
    # Content fingerprint of the registry document, so in-place agent edits
    # (enabled, status, endpoint, card metadata) trigger a live agents snapshot
    # even when the number of agents is unchanged.
    registry_fingerprint: str = ""
    build: BuildInfo | None = None
    sessions: SessionSummary | None = None
    # Content fingerprint of the MCP server config. ``tools`` already has one
    # entry per server, so add/remove is caught by count; this additionally
    # catches in-place edits (enable/disable, url/description) that leave the
    # count unchanged.
    mcp_fingerprint: str = ""
    # Content fingerprint of the knowledge state document, so an agent edit to
    # /config/knowledge.json (which leaves all the counted fields above unchanged)
    # is still detected and pushed live to the Knowledge page.
    knowledge_fingerprint: str = ""
    # Content fingerprint of the schedule state document. ``schedule`` count
    # catches add/remove; this additionally catches in-place edits (pause/resume,
    # message/cron changes) so an agent edit is pushed live to the Schedules
    # page even when the entry count is unchanged.
    schedule_fingerprint: str = ""
    # Content fingerprint of the kanban board (structure + all per-card
    # documents). Unlike mcp/knowledge/schedule, there is no other tracked
    # field anywhere in this snapshot that changes when a task is added,
    # edited, or removed (task count/content live only in Postgres, not in
    # cycle_count/messages_count/etc.), so without this the change-detection
    # in _state_changed() would never notice a kanban-only write and the
    # Tasks page would only pick up new/edited cards on a manual refresh.
    kanban_fingerprint: str = ""
    # Content fingerprint of the "health" Postgres document (status,
    # cycle_count, thread_id, idle_cycles, queue_stats, pid - NOT liveness,
    # which is a live SSE check pushed separately via _process_metrics()).
    health_fingerprint: str = ""
    # Content fingerprint of all inbox message documents. Like kanban, no other
    # snapshot field changes when the agent writes /inbox/<id>.md, so this is
    # the sole signal _state_changed() has for an inbox-only write.
    inbox_fingerprint: str = ""
    # Content fingerprint of all workflow definition, run, and queue documents.
    # Like kanban/inbox, no other snapshot field changes when the agent writes
    # /workflows/<id>.json (or the executor updates a run), so this is
    # the sole signal _state_changed() has for a workflow-only write.
    workflow_fingerprint: str = ""


class StateReader:
    """Reads state, session, config, and log data from the repo runtime dirs."""

    def __init__(
        self,
        document_store: BackendDocumentStore,
        logs_dir: Path | None = None,
    ):
        self.logs_dir = logs_dir or get_log_dir()
        self.document_store = document_store
        self._last_snapshot: StateSnapshot | None = None
        self._watchers: list[Callable[[StateSnapshot, str | None], None]] = []
        self._listener: StoreChangeListener | None = None

    @property
    def _resolved_logs_dir(self) -> Path:
        return self.logs_dir.resolve()

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
        health = self._get_health_document()
        health.live_reachable = _live_progena_reachable()
        return health

    def _get_health_document(self) -> HealthInfo:
        if self.document_store is not None:
            health_doc = self.document_store.get_document(PROGENA_STATE_PREFIX, "health", {})
            data = health_doc.payload if health_doc is not None else {}
            if isinstance(data, dict) and data:
                return HealthInfo.from_dict(data)
            # Brand-new DB, before the heartbeat loop has ever written a
            # "health" document - fall back to current_state for a rough
            # signal so the UI doesn't flash "unknown" on first boot.
            current_doc = self.document_store.get_document(
                PROGENA_STATE_PREFIX, "current_state", {}
            )
            current = current_doc.payload if current_doc is not None else {}
            if isinstance(current, dict):
                last_active = (
                    current.get("updated_at")
                    or current.get("last_active")
                    or (
                        current_doc.updated_at.isoformat()
                        if current_doc and current_doc.updated_at
                        else None
                    )
                )
                return HealthInfo(
                    status="running" if last_active else "unknown",
                    last_active=str(last_active) if last_active else None,
                    thread_id=current.get("thread_id"),
                    cycle_count=_coerce_int(current.get("cycle_count"), 0),
                    pid=None,
                )
        return HealthInfo(
            status="unknown", last_active=None, thread_id=None, cycle_count=0, pid=None
        )

    def get_registry(self) -> tuple[list[AgentInfo], list[ToolInfo]]:
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "registry", {})
        agents_data = document.payload if document is not None else {}

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
        return self._registry_tools(agents)

    def get_a2a_tasks(self) -> list[dict[str, Any]]:
        """Read Progena's tracked A2A task records (read-through).

        Progena owns the A2A task lifecycle and persists the records to its
        ``("progena","state","a2a_tasks")`` document; the backend just reads
        them so the Agents page can show per-agent task history.
        """
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "a2a_tasks", [])
        doc = document.payload if document is not None else []
        return [task for task in doc if isinstance(task, dict)] if isinstance(doc, list) else []

    def get_acp_tasks(self) -> list[dict[str, Any]]:
        """Read the per-record ACP task documents for the Agents page."""
        rows = self.document_store.list_documents(PROGENA_STATE_PREFIX, ACP_TASK_KEY_PREFIX)
        tasks: list[dict[str, Any]] = []
        for key, document in rows:
            if not isinstance(document.payload, dict):
                continue
            task = dict(document.payload)
            task.setdefault("task_id", key.removeprefix(ACP_TASK_KEY_PREFIX))
            tasks.append(task)
        return tasks

    def _registry_tools(self, agents: list[AgentInfo]) -> tuple[list[AgentInfo], list[ToolInfo]]:
        tools = self._mcp_tools_from_store()
        return agents, tools

    def _mcp_tools_from_store(self) -> list[ToolInfo]:
        data = self.get_mcp_config()
        raw_servers = data.get("mcpServers") if isinstance(data, dict) else None
        if not isinstance(raw_servers, dict):
            return []

        tools: list[ToolInfo] = []
        for name, raw_server in raw_servers.items():
            if not isinstance(name, str) or not isinstance(raw_server, dict):
                continue
            url = str(raw_server.get("url") or raw_server.get("endpoint") or "").strip()
            if not url:
                continue
            resolved_url, missing_vars = resolve_required_env_template(url)
            if resolved_url is None:
                logger.warning(
                    "Skipping MCP server '%s': unresolved env vars in url: %s",
                    name,
                    ", ".join(sorted(set(missing_vars))),
                )
                continue
            enabled = bool(raw_server.get("enabled", True))
            transport = str(raw_server.get("transport") or "http").strip().lower()
            tools.append(
                ToolInfo.from_dict(
                    {
                        "name": name,
                        "description": raw_server.get("description")
                        or f"Remote MCP server at {resolved_url}",
                        "enabled": enabled,
                        "kind": "mcp_server",
                        "status": "stopped" if enabled else "disabled",
                        "transport": transport,
                        "url": resolved_url,
                    }
                )
            )
        return tools

    def _mcp_tools_by_server(self) -> dict[str, list[dict[str, Any]]]:
        """Group the runtime-discovered MCP tools (with enabled flags) by server."""
        data = self.get_mcp_tools()
        raw_tools = data.get("tools") if isinstance(data, dict) else None
        grouped: dict[str, list[dict[str, Any]]] = {}
        if not isinstance(raw_tools, list):
            return grouped
        for entry in raw_tools:
            if not isinstance(entry, dict):
                continue
            server = str(entry.get("server") or "").strip()
            name = str(entry.get("name") or "").strip()
            if not server or not name:
                continue
            grouped.setdefault(server, []).append(
                {
                    "name": name,
                    "original_name": entry.get("original_name") or name,
                    "description": entry.get("description") or "",
                    "enabled": bool(entry.get("enabled", True)),
                }
            )
        return grouped

    def _mcp_server_status(self) -> dict[str, dict[str, Any]]:
        """Per-server discovery outcome (name -> {status, error, ...}).

        Written by the runtime on each MCP refresh; used to surface a failed
        server (e.g. auth error) instead of a silently empty one.
        """
        data = self.get_mcp_tools()
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, dict):
            return {}
        return {
            str(name): entry
            for name, entry in servers.items()
            if isinstance(name, str) and isinstance(entry, dict)
        }

    def get_mcp_config(self) -> dict[str, Any]:
        document = self.document_store.get_document(
            PROGENA_STATE_PREFIX,
            "mcp",
            default_mcp_config_payload(),
        )
        payload = document.payload if document is not None else default_mcp_config_payload()
        return normalize_mcp_config_payload(payload if isinstance(payload, dict) else None)

    def save_mcp_config(self, payload: dict[str, Any]) -> None:
        normalized = normalize_mcp_config_payload(payload)
        self.document_store.save_document(PROGENA_STATE_PREFIX, "mcp", normalized)

    def get_mcp_tools(self) -> dict[str, Any]:
        """Read the runtime-owned discovered MCP tools document."""
        default: dict[str, Any] = {"tools": []}
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "mcp_tools", default)
        payload = document.payload if document is not None else default
        return payload if isinstance(payload, dict) else dict(default)

    def save_mcp_tools(self, payload: dict[str, Any]) -> None:
        self.document_store.save_document(PROGENA_STATE_PREFIX, "mcp_tools", payload)

    def get_backlog(self) -> list[BacklogItem]:
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "build_state", {})
        data = document.payload if document is not None else {}
        backlog = data.get("backlog", []) if isinstance(data, dict) else []
        if not isinstance(backlog, list):
            return []
        return [BacklogItem.from_dict(item) for item in backlog if isinstance(item, dict)]

    def get_schedule(self) -> list[ScheduleItem]:
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        data = document.payload if document is not None else {}
        if not isinstance(data, dict):
            return []
        return [
            ScheduleItem.from_dict({**item, "id": item.get("id") or entry_id})
            for entry_id, item in data.items()
            if isinstance(item, dict)
        ]

    def get_event_widgets(self) -> list[EventWidgetItem]:
        document = self.document_store.get_document(BACKEND_STATE_PREFIX, "event_widgets", {})
        data = document.payload if document is not None else {}
        if not isinstance(data, dict):
            return []
        return [
            EventWidgetItem.from_dict({**item, "id": item.get("id") or entry_id})
            for entry_id, item in data.items()
            if isinstance(item, dict)
        ]

    def get_build_state(self) -> dict[str, Any]:
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, "build_state", {})
        data = document.payload if document is not None else {}
        return data if isinstance(data, dict) else {}

    def get_progena_state(self, thread_id: str | None = None) -> dict[str, Any]:
        key = f"current_state:{thread_id}" if thread_id is not None else "current_state"
        document = self.document_store.get_document(PROGENA_STATE_PREFIX, key, {})
        data = document.payload if document is not None else {}
        return data if isinstance(data, dict) else {}

    def get_sessions_summary(self) -> SessionSummary:
        document = self.document_store.get_document(BACKEND_STATE_PREFIX, "sessions", [])
        data = document.payload if document is not None else []
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

    def get_loops(self, health: HealthInfo) -> list[LoopInfo]:
        if not health.is_running():
            return []

        current = self.get_progena_state()
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

    def _registry_fingerprint(self) -> str:
        """Stable hash of the registry state document."""
        try:
            registry_doc = self.document_store.get_document(PROGENA_STATE_PREFIX, "registry", {})
            payload = registry_doc.payload if registry_doc is not None else {}
        except Exception:
            return ""
        return _hash_material(payload)

    def _mcp_fingerprint(self) -> str:
        """Stable hash of the MCP config *and* discovered tools/status.

        Includes the runtime-owned tools document (discovered tools + per-server
        status), not just the server config, so that tool discovery completing
        after a config write (or a server flipping to a failed state) is detected
        as a change and pushed live — otherwise the snapshot would only fire on
        the config edit, before any tools were discovered.
        """
        try:
            config = self.get_mcp_config()
        except Exception:
            return ""
        servers = config.get("mcpServers") if isinstance(config, dict) else None
        try:
            tools_doc = self.get_mcp_tools()
        except Exception:
            tools_doc = {}
        material = {
            "servers": servers or {},
            "tools": tools_doc.get("tools") if isinstance(tools_doc, dict) else None,
            "status": tools_doc.get("servers") if isinstance(tools_doc, dict) else None,
        }
        return _hash_material(material)

    def _knowledge_fingerprint(self) -> str:
        """Stable hash of the ``knowledge`` state document (base settings)."""
        if self.document_store is None:
            return ""
        try:
            document = self.document_store.get_document(
                PROGENA_STATE_PREFIX, "knowledge", {"bases": []}
            )
        except Exception:
            return ""
        payload = document.payload if document is not None else {"bases": []}
        return _hash_material(payload)

    def _schedule_fingerprint(self) -> str:
        """Stable hash of the ``schedule`` state document (all entries, keyed by id).

        The schedule count is checked separately for add/remove; this hash
        additionally catches in-place edits (pause/resume, message/cron changes)
        that leave the count unchanged, so the change is still pushed live to
        the Schedules page. Mirrors ``_knowledge_fingerprint``.
        """
        if self.document_store is None:
            return ""
        try:
            document = self.document_store.get_document(PROGENA_STATE_PREFIX, "schedule", {})
        except Exception:
            return ""
        payload = document.payload if document is not None else {}
        return _hash_material(payload)

    def _kanban_fingerprint(self) -> str:
        """Stable hash of the kanban board (structure + every per-card document).

        Mirrors ``_schedule_fingerprint``, but unlike schedule/knowledge/mcp
        there is no other snapshot field (cycle_count, messages_count, ...)
        that ever changes when a task is only added, edited, or removed, so
        this fingerprint is the sole signal ``_state_changed`` has for a
        kanban-only write.
        """
        if self.document_store is None:
            return ""
        try:
            structure_doc = self.document_store.get_document(
                PROGENA_STATE_PREFIX, "kanban_board", None
            )
            structure = structure_doc.payload if structure_doc is not None else {}
            order = structure.get("card_order") if isinstance(structure, dict) else None
            card_order = (
                [str(cid).strip() for cid in order if str(cid).strip()]
                if isinstance(order, list)
                else []
            )
            cards = []
            for card_id in card_order:
                card_doc = self.document_store.get_document(
                    PROGENA_STATE_PREFIX, f"kanban_card:{card_id}", None
                )
                if card_doc is not None and isinstance(card_doc.payload, dict):
                    cards.append(card_doc.payload)
            material = {"structure": structure, "cards": cards}
        except Exception:
            return ""
        return _hash_material(material)

    def _inbox_fingerprint(self) -> str:
        """Stable hash of all inbox message documents.

        Mirrors ``_kanban_fingerprint`` (no other snapshot field ever changes
        on an inbox-only write), but the messages have no index document, so
        this is a single key-prefix query instead of per-key gets.
        """
        if self.document_store is None:
            return ""
        try:
            rows = self.document_store.list_documents(PROGENA_STATE_PREFIX, "inbox_message:")
            material = [
                document.payload for _key, document in rows if isinstance(document.payload, dict)
            ]
        except Exception:
            return ""
        return _hash_material(material)

    def _workflow_fingerprint(self) -> str:
        """Stable hash of workflow definitions, runs, queues, and invoke requests.

        A request document is included because the always-on workflow invoke
        reconciler is registered as a StateReader watcher and must run when
        Progena writes a durable invoke request.
        """
        if self.document_store is None:
            return ""
        try:
            material: list[dict[str, Any]] = []
            for key_prefix in (
                WORKFLOW_DEFINITION_KEY_PREFIX,
                WORKFLOW_RUN_KEY_PREFIX,
                WORKFLOW_QUEUE_KEY_PREFIX,
                WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
            ):
                rows = self.document_store.list_documents(PROGENA_STATE_PREFIX, key_prefix)
                material.extend(
                    document.payload
                    for _key, document in rows
                    if isinstance(document.payload, dict)
                )
        except Exception:
            return ""
        return _hash_material(material)

    def _health_fingerprint(self) -> str:
        """Stable hash of the ``health`` Postgres document's status fields.

        Deliberately excludes liveness (``HealthInfo.live_reachable``) - that
        signal is a live SSE check, not stored in Postgres, so a pure liveness
        flip with nothing else changing would never trigger this fingerprint.
        See ``_process_metrics()`` in monitoring/status.py for how liveness reaches the
        frontend instead (pushed on its own independent ~3s tick).
        """
        if self.document_store is None:
            return ""
        try:
            health_doc = self.document_store.get_document(PROGENA_STATE_PREFIX, "health", {})
            payload = health_doc.payload if health_doc is not None else {}
        except Exception:
            return ""
        return _hash_material(payload)

    def get_snapshot(self) -> StateSnapshot:
        health = self.get_health()
        agents, tools = self.get_registry()
        backlog = self.get_backlog()
        schedule = self.get_schedule()
        loops = self.get_loops(health)
        progena = self.get_progena_state()
        build = self.get_build_state()
        return StateSnapshot(
            timestamp=time.time(),
            health=health,
            agents=agents,
            tools=tools,
            backlog=backlog,
            schedule=schedule,
            loops=loops,
            cycle_count=progena.get("cycle_count", 0),
            thread_id=progena.get("thread_id"),
            messages_count=len(progena.get("messages", [])),
            build=BuildInfo.from_dict(build),
            sessions=self.get_sessions_summary(),
            mcp_fingerprint=self._mcp_fingerprint(),
            registry_fingerprint=self._registry_fingerprint(),
            knowledge_fingerprint=self._knowledge_fingerprint(),
            schedule_fingerprint=self._schedule_fingerprint(),
            kanban_fingerprint=self._kanban_fingerprint(),
            health_fingerprint=self._health_fingerprint(),
            inbox_fingerprint=self._inbox_fingerprint(),
            workflow_fingerprint=self._workflow_fingerprint(),
        )

    def _iter_log_files(self) -> list[Path]:
        if not self.logs_dir.exists():
            return []
        files = [path for path in self.logs_dir.rglob("*") if path.is_file()]
        files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
        return files

    def _relative_log_path(self, path: Path) -> str:
        resolved_logs_dir = self._resolved_logs_dir
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
            parsed_timestamp = _parse_timestamp(matched.group("timestamp"))
            timestamp = (
                _iso_from_datetime(parsed_timestamp)
                if parsed_timestamp is not None
                else matched.group("timestamp")
            )
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
        resolved_logs_dir = self._resolved_logs_dir
        resolved_path = path.resolve()
        relative_parts = (
            resolved_path.relative_to(resolved_logs_dir).parts
            if resolved_path.is_relative_to(resolved_logs_dir)
            else resolved_path.parts
        )
        for part in reversed(relative_parts[:-1]):
            if part not in {"logs", "progena"}:
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

        resolved_logs_dir = self._resolved_logs_dir

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
                file_entry = self._line_to_entry(line, path_source, path)
                if not file_entry:
                    continue
                entry_timestamp = _parse_timestamp(file_entry.get("timestamp"))
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
                if level and file_entry["level"] != level.lower():
                    continue
                if folder_lc and file_entry["group"].lower() != folder_lc:
                    continue
                if file_path_lc and file_entry["relative_path"].lower() != file_path_lc:
                    continue
                if source_lc and not self._entry_matches_source(file_entry, source_lc):
                    continue
                if search:
                    searchable = self._entry_search_text(file_entry)
                    if regex and not regex.search(searchable):
                        continue
                    if not regex and search_lc and search_lc not in searchable:
                        continue
                entries.append(file_entry)

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

    def get_widgets(self) -> list[dict[str, Any]]:
        health = self.get_health()
        agents, tools = self.get_registry()
        schedule = self.get_schedule()
        event_widgets = self.get_event_widgets()

        widgets: list[dict[str, Any]] = []
        for agent in agents:
            if not agent.enabled:
                continue
            status = self._status_with_health(agent.status, health.is_running())
            summary = agent.status_message or agent.description or f"{agent.name} is {status}"
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
                    "updated_at": agent.updated_at or health.last_active,
                }
            )

        tools_by_server = self._mcp_tools_by_server()
        server_status = self._mcp_server_status()
        for tool in tools:
            status = self._status_with_health(tool.status, health.is_running())
            status_message: str | None = None
            # A connected-but-failed server (e.g. auth error) reports its reason
            # via the runtime status map; surface it as an error state + summary.
            outcome = server_status.get(tool.name)
            if tool.enabled and isinstance(outcome, dict) and outcome.get("status") == "error":
                status = "error"
                status_message = str(outcome.get("error") or "Discovery failed")
            summary = status_message or tool.description or f"MCP {tool.name}"
            widgets.append(
                {
                    "id": f"mcp:{tool.name}",
                    "type": "mcp",
                    "name": tool.name,
                    "title": tool.name,
                    "description": tool.description,
                    "kind": tool.kind,
                    "status": status,
                    "status_message": status_message,
                    "summary": summary,
                    "transport": tool.transport,
                    "enabled": tool.enabled,
                    "url": tool.url,
                    "tools": tools_by_server.get(tool.name, []),
                    "current_thread_id": health.thread_id,
                    "updated_at": health.last_active,
                }
            )

        for schedule_item in schedule:
            is_completed_one_off = (
                bool(schedule_item.raw.get("scheduled_at"))
                and not schedule_item.enabled
                and (
                    schedule_item.raw.get("last_run_status") == "completed"
                    or (
                        schedule_item.raw.get("last_run")
                        and not schedule_item.raw.get("next_run_at")
                        and not schedule_item.next_run
                    )
                )
            )
            widgets.append(
                {
                    "id": f"schedule:{schedule_item.id}",
                    "type": "schedule",
                    "name": schedule_item.name,
                    "title": schedule_item.name or schedule_item.id,
                    "description": schedule_item.raw.get("user_request") or schedule_item.name,
                    "status": (
                        "completed"
                        if is_completed_one_off
                        else "enabled"
                        if schedule_item.enabled
                        else "disabled"
                    ),
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
                "version": snapshot.health.version,
                "started_at": snapshot.health.started_at,
                "llm_model": snapshot.health.llm_model,
                "llm_reasoning_enabled": snapshot.health.llm_reasoning_enabled,
                "llm_reasoning_effort": snapshot.health.llm_reasoning_effort,
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
                "thread_id": snapshot.health.thread_id,
                "cycle_count": snapshot.health.cycle_count,
                "pid": snapshot.health.pid,
                "is_running": snapshot.health.is_running(),
                "version": snapshot.health.version,
                "started_at": snapshot.health.started_at,
                "llm_model": snapshot.health.llm_model,
                "llm_reasoning_enabled": snapshot.health.llm_reasoning_enabled,
                "llm_reasoning_effort": snapshot.health.llm_reasoning_effort,
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

    def add_watcher(self, callback: Callable[[StateSnapshot, str | None], None]) -> None:
        self._watchers.append(callback)

    def remove_watcher(self, callback: Callable[[StateSnapshot, str | None], None]) -> None:
        if callback in self._watchers:
            self._watchers.remove(callback)

    async def start_watching(self, reconnect_delay: float = 2.0) -> None:
        """Push state changes to watchers via Postgres LISTEN/NOTIFY (no polling).

        ``reconnect_delay`` is the backoff between listener reconnect attempts.
        With a non-Postgres store (in-memory/tests) there is nothing to listen
        to, so this is a no-op.
        """
        if self._listener is not None:
            return
        database_url = store_database_url(self.document_store)
        if database_url is None:
            return
        self._listener = StoreChangeListener(
            database_url,
            on_change=self._on_store_change,
            on_resync=self._emit_if_changed,
            reconnect_delay=reconnect_delay,
        )
        await self._listener.start()

    async def stop_watching(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None

    def _on_store_change(self, payload: str) -> None:
        # The NOTIFY payload is {"prefix": ..., "key": ..., "action": ...};
        # pass the key so watchers can route to the right dedicated event
        # instead of recomputing everything.
        changed_key: str | None = None
        try:
            data = json.loads(payload) if payload else {}
            key_value = data.get("key") if isinstance(data, dict) else None
            changed_key = key_value if isinstance(key_value, str) else None
        except (ValueError, TypeError):
            changed_key = None
        self._emit_if_changed(changed_key)

    def _emit_if_changed(self, changed_key: str | None = None) -> None:
        """Recompute the snapshot and notify watchers when something changed.

        ``changed_key`` is the store key that triggered the change (None on a
        full resync, e.g. after a listener reconnect).
        """
        try:
            snapshot = self.get_snapshot()
        except Exception as e:
            logger.error("Failed to read state snapshot: %s", e)
            return
        if not self._state_changed(snapshot):
            # A sibling key's notification can already have consumed the
            # snapshot diff for a committed transaction. Dedicated collections
            # must still receive their own key's write, including metadata-only
            # schedule/inbox edits and the workflow/document families below.
            key_bypasses_state_diff = bool(
                changed_key
                and (
                    changed_key.startswith("policy:")
                    or changed_key == "schedule"
                    or changed_key in (MODEL_PROFILES_DOC_KEY, INDEX_STATUS_DOC_KEY)
                    or changed_key.startswith(INBOX_MESSAGE_KEY_PREFIX)
                    or changed_key.startswith(WORKFLOW_DEFINITION_KEY_PREFIX)
                    or changed_key.startswith(WORKFLOW_RUN_KEY_PREFIX)
                    or changed_key.startswith(WORKFLOW_QUEUE_KEY_PREFIX)
                    or changed_key.startswith(WORKFLOW_INVOKE_REQUEST_KEY_PREFIX)
                    or changed_key.startswith(AGENT_DOCUMENT_KEY_PREFIX)
                    or changed_key.startswith(SKILL_DOCUMENT_KEY_PREFIX)
                    or changed_key.startswith(ROUTINE_GRAPH_KEY_PREFIX)
                    or changed_key.startswith(REPO_KEY_PREFIX)
                    or changed_key.startswith(KNOWLEDGE_PAGE_KEY_PREFIX)
                    or changed_key.startswith(CANVAS_DOCUMENT_KEY_PREFIX)
                    or changed_key.startswith(CANVAS_DATA_KEY_PREFIX)
                    or changed_key.startswith(CANVAS_STATUS_KEY_PREFIX)
                )
            )
            # Reconnect must reach reconciliation even with unchanged state.
            if changed_key is not None and not key_bypasses_state_diff:
                return
        self._last_snapshot = snapshot
        for watcher in list(self._watchers):
            try:
                watcher(snapshot, changed_key)
            except Exception as e:
                logger.warning("Watcher error: %s", e)

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
        if new.knowledge_fingerprint != old.knowledge_fingerprint:
            return True
        if new.mcp_fingerprint != old.mcp_fingerprint:
            return True
        if new.schedule_fingerprint != old.schedule_fingerprint:
            return True
        if new.kanban_fingerprint != old.kanban_fingerprint:
            return True
        if new.registry_fingerprint != old.registry_fingerprint:
            return True
        if new.health_fingerprint != old.health_fingerprint:
            return True
        if new.inbox_fingerprint != old.inbox_fingerprint:
            return True
        if new.workflow_fingerprint != old.workflow_fingerprint:
            return True
        return (new.sessions.total if new.sessions else 0) != (
            old.sessions.total if old.sessions else 0
        )


_state_reader: StateReader | None = None


def get_state_reader() -> StateReader:
    """Get or create the global state reader."""
    global _state_reader
    if _state_reader is None:
        _state_reader = StateReader(document_store=get_default_document_store())
    return _state_reader
