"""Monitoring status."""

from __future__ import annotations

import asyncio
import logging
import time

import app.backend.agents.routes as agents_routes
import app.backend.agents.store as agents_store
import app.backend.alerts.routes as alerts_routes
import app.backend.chat.routes as chat_routes
import app.backend.events.stream as events_stream
import app.backend.inbox.routes as inbox_routes
import app.backend.instructions.routes as instructions_routes
import app.backend.integrations.runtime as integrations_runtime
import app.backend.knowledge.routes as knowledge_routes
import app.backend.lifecycle.state as lifecycle_state
import app.backend.modules.routes as modules_routes
import app.backend.monitoring.state as monitoring_state
import app.backend.plugins.marketplace_routes as plugins_marketplace_routes
import app.backend.plugins.routes as plugins_routes
import app.backend.repositories.routes as repositories_routes
import app.backend.repositories.service as repositories_service
import app.backend.schedules.service as schedules_service
import app.backend.settings.mcp as settings_mcp
import app.backend.settings.preferences as settings_preferences
import app.backend.storage.documents as storage_documents
import app.backend.tasks.service as tasks_service
import app.backend.workflows.runtime as workflows_runtime
from app.progenitor.content_documents import AGENT_DOCUMENT_KEY_PREFIX, SKILL_DOCUMENT_KEY_PREFIX
from app.progenitor.document_kinds import (
    INSTRUCTION_DOCUMENT_KEY_PREFIX,
    KNOWLEDGE_PAGE_KEY_PREFIX,
    MODULE_DOCUMENT_KEY_PREFIX,
    MODULE_STATUS_KEY_PREFIX,
    REPO_KEY_PREFIX,
)
from app.progenitor.knowledge.indexer_config import INDEX_STATUS_DOC_KEY
from app.progenitor.workflows import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)

logger = logging.getLogger(__name__)

_status_monitor_task: asyncio.Task | None = None


_state_watch_push_task: asyncio.Task | None = None


_active_agent_profile_broadcast_tasks: set[asyncio.Task] = set()


_active_model_profile_broadcast_tasks: set[asyncio.Task] = set()


async def _get_current_status() -> dict:
    """Get current server status for SSE broadcast."""

    state_reader = monitoring_state.get_state_reader()
    snapshot = state_reader.get_snapshot()

    now = time.time()
    start_time = getattr(lifecycle_state, "_server_start_time", now)
    shutdown_mgr = getattr(lifecycle_state, "shutdown_manager", None)

    status = {
        "status": "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": snapshot.health.is_running(),
        "progenitor_status": snapshot.health.status,  # Raw status from the health document
        "version": getattr(lifecycle_state.config, "api_version", "0.1.0"),
        # State data
        "cycle_count": snapshot.health.cycle_count,  # Use health.cycle_count for accuracy
        "thread_id": snapshot.health.thread_id or snapshot.thread_id,
        "messages_count": snapshot.messages_count,
        "pid": snapshot.health.pid,
        "last_active": snapshot.health.last_active,
        # Distinct from "version" above (the backend's own api_version) - this
        # is the runtime process's code commit, for spotting a stale-process
        # running old code without cross-referencing Postgres by hand.
        "progenitor_commit": snapshot.health.version,
        "progenitor_started_at": snapshot.health.started_at,
        "llm_model": snapshot.health.llm_model,
        "llm_reasoning_enabled": snapshot.health.llm_reasoning_enabled,
        "llm_reasoning_effort": snapshot.health.llm_reasoning_effort,
        # Loops
        "loops": [
            {
                "name": loop_item.name,
                "status": loop_item.status,
                "current_task": loop_item.current_task,
            }
            for loop_item in snapshot.loops
        ],
        # Agents
        "agents": [
            {
                "name": a.name,
                "kind": a.kind,
                "version": a.version,
                "description": a.description,
                "enabled": a.enabled,
                "status": "ready" if snapshot.health.is_running() else "offline",
            }
            for a in snapshot.agents
        ],
        "tools": [
            {"name": tool.name, "description": tool.description, "enabled": tool.enabled}
            for tool in snapshot.tools
        ],
        # Backlog
        "backlog": [
            {"id": b.id, "type": b.item_type, "status": b.status, "description": b.description}
            for b in snapshot.backlog
        ],
        # Schedule
        "schedule": [
            {"id": s.id, "name": s.name, "schedule": s.schedule, "enabled": s.enabled}
            for s in snapshot.schedule
        ],
        # Build status
        "build": {
            "in_progress": snapshot.build.in_progress if snapshot.build else False,
            "phase": snapshot.build.phase if snapshot.build else None,
            "task_description": snapshot.build.task_description if snapshot.build else None,
            "started_at": snapshot.build.started_at if snapshot.build else None,
            "iteration": snapshot.build.iteration if snapshot.build else 0,
            "score": snapshot.build.score if snapshot.build else 0.0,
            "message_count": snapshot.build.message_count if snapshot.build else 0,
        }
        if snapshot.build
        else None,
        "widgets": state_reader.get_widgets(),
        "sessions": {
            "total": snapshot.sessions.total if snapshot.sessions else 0,
            "latest_session_id": snapshot.sessions.latest_session_id if snapshot.sessions else None,
            "latest_activity": snapshot.sessions.latest_activity if snapshot.sessions else None,
        },
        "alerts": alerts_routes._alerts_payload(),
    }

    if shutdown_mgr and shutdown_mgr.is_shutting_down:
        status["status"] = "draining"

    # Memory info
    try:
        import psutil

        process = psutil.Process()
        status["memory_mb"] = round(process.memory_info().rss / 1024 / 1024, 2)
    except ImportError:
        status["memory_mb"] = None
    except Exception:
        status["memory_mb"] = None

    return status


def _process_metrics() -> dict:
    """Process-level status that changes without a DB write (uptime, memory).

    The full, DB-derived status is pushed on connect (connection.ready) and on
    every change via the Postgres NOTIFY watcher; this lightweight payload is
    merged into it by the periodic tick so we never re-read the DB just to
    refresh metrics.

    Also includes ``progenitor_running``: a live SSE-heartbeat liveness check,
    deliberately NOT stored in Postgres (see HealthInfo.live_reachable), so a
    pure liveness flip - the runtime dying with no other document changing -
    still reaches the frontend within this tick's cadence instead of only
    surfacing on the next incidental Postgres write.
    """

    now = time.time()
    start_time = getattr(lifecycle_state, "_server_start_time", now)
    shutdown_mgr = getattr(lifecycle_state, "shutdown_manager", None)
    draining = bool(shutdown_mgr and shutdown_mgr.is_shutting_down)

    metrics: dict = {
        "status": "draining" if draining else "healthy",
        "uptime_seconds": round(now - start_time, 1),
        "progenitor_running": bool(integrations_runtime.get_progenitor_liveness()),
    }
    try:
        import psutil

        metrics["memory_mb"] = round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
    except Exception:
        metrics["memory_mb"] = None
    return metrics


async def _status_monitor():
    """Push process-level metrics to SSE subscribers on a fixed cadence.

    Only metrics (uptime/memory) are sent here; DB-derived state is carried by
    the LISTEN/NOTIFY watcher, so this loop never reads the database.
    """
    update_interval = 3  # Refresh process metrics every 3 seconds

    logger.info("[StatusMonitor] Starting process-metrics loop")

    while True:
        try:
            await asyncio.sleep(update_interval)

            if not events_stream._ui_stream_subscribers:
                continue  # No subscribers, skip

            events_stream.broadcast_ui_event("status.updated", _process_metrics())

        except asyncio.CancelledError:
            print("[StatusMonitor] Cancelled", flush=True)
            break
        except Exception as e:
            print(f"[StatusMonitor] Error: {e}", flush=True)
            await asyncio.sleep(10)


async def _broadcast_latest_status() -> None:
    global _state_watch_push_task
    try:
        current = await _get_current_status()
        events_stream.broadcast_ui_event("status.updated", current)
        if events_stream._ui_stream_subscribers:
            await chat_routes._broadcast_chat_sessions_snapshot()
    finally:
        _state_watch_push_task = None


_WIDGET_KEYS = frozenset({"mcp", "mcp_tools", "schedule", "registry", "event_widgets"})


async def _broadcast_agent_profiles_safe() -> None:
    try:
        await agents_routes.broadcast_agent_profiles_snapshot(
            agents_store.get_agent_store, events_stream.broadcast_ui_event
        )
    except Exception:
        logger.debug("Failed to broadcast agent profiles snapshot", exc_info=True)


async def _broadcast_model_profiles_safe() -> None:
    try:
        from app.backend.settings.model_profiles import broadcast_model_profiles_snapshot

        await broadcast_model_profiles_snapshot(
            storage_documents.get_default_document_store(), events_stream.broadcast_ui_event
        )
    except Exception:
        logger.debug("Failed to broadcast model profiles snapshot", exc_info=True)


def _queue_state_watch_status(snapshot, changed_key: str | None = None) -> None:
    global _state_watch_push_task
    if not events_stream._ui_stream_subscribers:
        return
    try:
        if (
            changed_key is None
            or changed_key == tasks_service._kanban_board_store_key()
            or changed_key.startswith(tasks_service._KANBAN_CARD_KEY_PREFIX)
        ):
            tasks_service.broadcast_kanban_snapshot()
        if changed_key is None or changed_key.startswith(inbox_routes.INBOX_MESSAGE_KEY_PREFIX):
            inbox_routes._broadcast_inbox_snapshot()
        if changed_key in (None, "mcp", "mcp_tools"):
            settings_mcp.broadcast_mcp_snapshot()
        if changed_key in (None, "schedule"):
            schedules_service.broadcast_schedule_snapshot()
        if changed_key in (None, "knowledge", INDEX_STATUS_DOC_KEY):
            knowledge_routes.broadcast_knowledge_snapshot()
        if changed_key is not None and changed_key.startswith(KNOWLEDGE_PAGE_KEY_PREFIX):
            base_id = changed_key.removeprefix(KNOWLEDGE_PAGE_KEY_PREFIX).split("/", 1)[0]
            if base_id:
                knowledge_routes.broadcast_knowledge_pages_snapshot(base_id)
            knowledge_routes.broadcast_knowledge_snapshot()
        if changed_key is None or changed_key.startswith(INSTRUCTION_DOCUMENT_KEY_PREFIX):
            instructions_routes.broadcast_instructions_snapshot()
        if changed_key in (None, "preferences"):
            settings_preferences.broadcast_preferences_snapshot()
        if changed_key in (None, "marketplaces"):
            plugins_marketplace_routes.broadcast_marketplaces_snapshot()
        if changed_key in (None, "plugins"):
            plugins_routes.broadcast_plugins_snapshot()
        if (
            changed_key is None
            or changed_key.startswith(MODULE_DOCUMENT_KEY_PREFIX)
            or changed_key.startswith(MODULE_STATUS_KEY_PREFIX)
        ):
            modules_routes.broadcast_modules_snapshot()
        if changed_key in (None, "profile"):
            profile_task = asyncio.create_task(_broadcast_model_profiles_safe())
            _active_model_profile_broadcast_tasks.add(profile_task)
            profile_task.add_done_callback(_active_model_profile_broadcast_tasks.discard)
        if (
            changed_key is None
            or changed_key == "registry"
            or changed_key.startswith(AGENT_DOCUMENT_KEY_PREFIX)
            or changed_key.startswith(SKILL_DOCUMENT_KEY_PREFIX)
        ):
            profile_task = asyncio.create_task(_broadcast_agent_profiles_safe())
            _active_agent_profile_broadcast_tasks.add(profile_task)
            profile_task.add_done_callback(_active_agent_profile_broadcast_tasks.discard)
        if changed_key is None:
            board = tasks_service._read_kanban_board(monitoring_state.get_state_reader())
            for card in board["cards"]:
                card_id = str(card.get("id") or "").strip()
                if card_id:
                    tasks_service.broadcast_task_content(card_id)
                    tasks_service.broadcast_task_activity(card_id)
        elif changed_key.startswith("kanban_task_content:"):
            card_id = changed_key.split(":", 1)[1]
            tasks_service.broadcast_task_content(card_id)
            tasks_service.broadcast_task_activity(card_id)
        if changed_key is None or changed_key.startswith(WORKFLOW_DEFINITION_KEY_PREFIX):
            workflows_runtime.broadcast_workflows_snapshot()
            if changed_key is not None:
                workflow_id = changed_key.removeprefix(WORKFLOW_DEFINITION_KEY_PREFIX)
                if workflow_id:
                    workflows_runtime.broadcast_workflow_detail(workflow_id)
        if (
            changed_key is None
            or changed_key.startswith(WORKFLOW_RUN_KEY_PREFIX)
            or changed_key.startswith(WORKFLOW_QUEUE_KEY_PREFIX)
        ):
            workflows_runtime.broadcast_workflow_runs_snapshot()
        if changed_key in (None, "repo_scan") or changed_key.startswith(REPO_KEY_PREFIX):
            repositories_routes.broadcast_repositories_snapshot()
            repo_id = (changed_key or "").removeprefix(REPO_KEY_PREFIX)
            # A deleted repo has no detail left to show.
            if (
                changed_key
                and changed_key != repo_id
                and repositories_service.load_repo(repo_id) is not None
            ):
                repositories_routes.broadcast_repo_detail(repo_id)
        if changed_key is None or changed_key in _WIDGET_KEYS:
            schedules_service.broadcast_widgets_snapshot()
    except Exception:
        logger.debug("Failed to broadcast dedicated snapshot", exc_info=True)
    if _state_watch_push_task and not _state_watch_push_task.done():
        return
    _state_watch_push_task = asyncio.create_task(_broadcast_latest_status())


def _status_changed(current: dict, previous: dict) -> bool:
    """Check if status has meaningfully changed."""
    if not previous:
        return True

    # Check key fields for changes
    for key in ("status", "progenitor_running"):
        if current.get(key) != previous.get(key):
            return True

    # Check memory change > 10MB
    curr_mem: float = current.get("memory_mb") or 0
    prev_mem: float = previous.get("memory_mb") or 0
    return abs(curr_mem - prev_mem) > 10


def start_status_monitor():
    """Start the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task is None or _status_monitor_task.done():
        _status_monitor_task = asyncio.create_task(_status_monitor())
        print("[StatusMonitor] Started background task", flush=True)


def stop_status_monitor():
    """Stop the status monitor background task."""
    global _status_monitor_task
    if _status_monitor_task and not _status_monitor_task.done():
        _status_monitor_task.cancel()
        logger.info("Status monitor stopped")


def broadcast_status_change(status: str, details: dict | None = None):
    """Broadcast a status change to all subscribers."""
    events_stream.broadcast_ui_event(
        "status.updated", {"status": status, "details": details or {}, "timestamp": time.time()}
    )
