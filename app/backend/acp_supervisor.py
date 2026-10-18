"""Backend-owned ACP task supervisor.

ACP task documents are durable requests.  This module is the only component that
opens ACP connections and mutates supervisor-owned fields on those requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.progenitor.acp import (
    ACPRunner,
    acp_config_from_agent_config,
    runner_for,
    task_command,
    task_cwd,
)
from app.progenitor.document_kinds import ACP_TASK_KEY_PREFIX
from app.progenitor.state.models import ACPTaskRecord, ACPTaskStatus
from app.progenitor.utils import utcnow

from .document_events import TypedDocumentEvent
from .document_store import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    document_write_origin,
)

logger = logging.getLogger(__name__)

_SUPERVISOR_ORIGIN = "acp_supervisor"
_HISTORY_LIMIT = 100
_TOOL_CALL_LIMIT = 200


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _now() -> str:
    return utcnow().isoformat()


def _task_id_from_key(key: str) -> str | None:
    task_id = key.removeprefix(ACP_TASK_KEY_PREFIX)
    return task_id if task_id and task_id != key else None


def _record(task_id: str, payload: Any) -> ACPTaskRecord | None:
    if not isinstance(payload, dict):
        return None
    normalized = dict(payload)
    normalized.setdefault("task_id", task_id)
    try:
        return ACPTaskRecord.model_validate(normalized)
    except Exception:
        logger.warning("Ignoring malformed ACP task %s", task_id, exc_info=True)
        return None


def _registry_agents(payload: Any) -> list[dict[str, Any]]:
    """Normalize the registry exactly like ``ToolRegistry`` does."""
    if not isinstance(payload, dict):
        return []
    agents = [item for item in payload.get("agents", []) if isinstance(item, dict)]
    agent_map = {
        str(name): config
        for name, config in payload.items()
        if name not in {"agents", "tools"} and isinstance(config, dict)
    }
    if not agent_map:
        return agents
    names = set(agent_map)
    return [
        *[item for item in agents if str(item.get("name") or "") not in names],
        *[{"name": name, **config} for name, config in agent_map.items()],
    ]


def resolve_acp_agent_config(store: BackendDocumentStore, agent_name: str) -> dict[str, Any]:
    """Resolve an enabled ACP registry entry by name."""
    document = store.get_document(PROGENITOR_STATE_PREFIX, "registry", {})
    for agent in _registry_agents(document.payload if document is not None else {}):
        if str(agent.get("name") or "") != agent_name:
            continue
        if agent.get("enabled", True) is False:
            raise ValueError(f"ACP agent '{agent_name}' is disabled")
        if str(agent.get("transport") or "").lower() != "acp":
            raise ValueError(f"Agent '{agent_name}' is not an ACP agent")
        acp_config_from_agent_config(agent)
        return agent
    raise ValueError(f"Unknown ACP agent: {agent_name}")


@dataclass
class _ActiveTurn:
    task_id: str
    turn: int
    prompt: str
    runner: ACPRunner
    progress: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    session_id: str | None
    last_persisted: float = field(default_factory=time.monotonic)
    dirty: bool = False
    stalled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ACPTaskSupervisor:
    """Drive queued ACP task documents with a bounded number of sessions."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        runner_factory: Callable[[Any, str], ACPRunner] = runner_for,
    ) -> None:
        self._store_getter = store_getter
        self._runner_factory = runner_factory
        self._max_sessions = _positive_int("PROGENITOR_ACP_MAX_SESSIONS", 2)
        self._max_turns = _positive_int("PROGENITOR_ACP_MAX_TURNS", 20)
        self._progress_interval = _positive_float("PROGENITOR_ACP_PROGRESS_INTERVAL_SECONDS", 15.0)
        self._stall_seconds = _positive_float("PROGENITOR_ACP_STALL_SECONDS", 600.0)
        self._semaphore = asyncio.Semaphore(self._max_sessions)
        self._scheduled: dict[str, asyncio.Task[None]] = {}
        self._active: dict[str, _ActiveTurn] = {}
        self._started = False
        self._stopping = False
        self._watchdog: asyncio.Task[None] | None = None

    def _store(self) -> BackendDocumentStore:
        return self._store_getter()

    def _mutate(self, task_id: str, mutator: Callable[[Any], Any]) -> Any:
        with document_write_origin(_SUPERVISOR_ORIGIN):
            return self._store().mutate_document(
                PROGENITOR_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", mutator
            )

    def _get(self, task_id: str) -> ACPTaskRecord | None:
        document = self._store().get_document(
            PROGENITOR_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", None
        )
        return _record(task_id, document.payload if document is not None else None)

    async def start(self) -> None:
        """Migrate legacy records, recover interrupted work, and queue pending tasks."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._migrate_legacy_records()
        rows = self._store().list_documents(PROGENITOR_STATE_PREFIX, ACP_TASK_KEY_PREFIX)
        for key, document in rows:
            task_id = _task_id_from_key(key)
            if task_id is None:
                continue
            record = _record(task_id, document.payload)
            if record is None:
                continue
            if record.status == ACPTaskStatus.WORKING.value:
                self._mark_interrupted(task_id)
            elif record.status == ACPTaskStatus.QUEUED.value:
                self.schedule(task_id)
        self._watchdog = asyncio.create_task(self._watch_for_stalls())

    async def stop(self) -> None:
        """Cancel in-flight turns before the backend tears down its connections."""
        self._stopping = True
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        for active in list(self._active.values()):
            await active.runner.cancel(active.session_id)
        scheduled = list(self._scheduled.values())
        if scheduled:
            done, pending = await asyncio.wait(scheduled, timeout=10.0)
            _ = done
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self._scheduled.clear()
        self._active.clear()
        self._started = False

    async def handle_document_event(self, typed: TypedDocumentEvent) -> None:
        """React to request-side writes to one ACP task document."""
        if typed.document_type is None or typed.document_type.id != "acp_task":
            return
        task_id = _task_id_from_key(typed.event.key)
        if task_id is None:
            return
        if typed.event.op == "deleted":
            active = self._active.get(task_id)
            if active is not None:
                await active.runner.cancel(active.session_id)
            return
        record = _record(task_id, typed.event.after)
        if record is None:
            return
        if record.cancel_requested:
            await self._cancel_requested(task_id)
        elif record.status == ACPTaskStatus.QUEUED.value:
            active = self._active.get(task_id)
            if active is not None:
                await active.runner.cancel(active.session_id)
            else:
                self.schedule(task_id)

    def schedule(self, task_id: str) -> None:
        """Queue work without changing a task's queued status while it waits."""
        if self._stopping:
            return
        existing = self._scheduled.get(task_id)
        # The task driving a turn reschedules itself after a nudge, so only a
        # different pending task counts as already scheduled.
        if existing is not None and not existing.done() and existing is not asyncio.current_task():
            return
        task = asyncio.create_task(self._wait_for_slot(task_id))
        self._scheduled[task_id] = task

        def _discard(completed: asyncio.Task[None]) -> None:
            if self._scheduled.get(task_id) is completed:
                self._scheduled.pop(task_id, None)

        task.add_done_callback(_discard)

    async def _wait_for_slot(self, task_id: str) -> None:
        try:
            record = self._get(task_id)
            if record is None or record.status != ACPTaskStatus.QUEUED.value:
                return
            if record.cancel_requested:
                await self._cancel_requested(task_id)
                return
            if record.turn >= self._max_turns:
                self._fail_queued(
                    task_id,
                    f"ACP task exceeded PROGENITOR_ACP_MAX_TURNS ({self._max_turns})",
                )
                return
            async with self._semaphore:
                if self._stopping:
                    return
                record = self._get(task_id)
                if record is None or record.status != ACPTaskStatus.QUEUED.value:
                    return
                if record.cancel_requested:
                    await self._cancel_requested(task_id)
                    return
                if record.turn >= self._max_turns:
                    self._fail_queued(
                        task_id,
                        f"ACP task exceeded PROGENITOR_ACP_MAX_TURNS ({self._max_turns})",
                    )
                    return
                try:
                    agent = resolve_acp_agent_config(self._store(), record.agent_name)
                    acp_config = acp_config_from_agent_config(agent)
                except ValueError as exc:
                    self._fail_queued(task_id, str(exc))
                    return
                claimed = self._claim(task_id, record, task_command(acp_config))
                if claimed is None:
                    return
                await self._drive_turn(claimed, acp_config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ACP supervisor failed for task %s", task_id)
            self._fail_queued(task_id, "ACP supervisor failed before the task could start")

    def _claim(
        self,
        task_id: str,
        expected: ACPTaskRecord,
        command: str,
    ) -> ACPTaskRecord | None:
        claimed: ACPTaskRecord | None = None

        def claim(current: Any) -> Any:
            nonlocal claimed
            record = _record(task_id, current)
            if record is None:
                return current
            payload = dict(current)
            if (
                record.status != ACPTaskStatus.QUEUED.value
                or record.cancel_requested
                or record.agent_name != expected.agent_name
                or record.prompt != expected.prompt
            ):
                return payload
            now = _now()
            progress = dict(payload.get("progress") or {})
            progress.setdefault("last_message", None)
            progress.setdefault("plan", [])
            progress.setdefault("tool_call_count", 0)
            progress.setdefault("last_tool", None)
            progress["last_activity_at"] = now
            payload["task_id"] = task_id
            payload["status"] = ACPTaskStatus.WORKING.value
            payload["turn"] = record.turn + 1
            payload["command"] = command
            payload["progress"] = progress
            payload["started_at"] = payload.get("started_at") or now
            payload["updated_at"] = now
            payload["completed_at"] = None
            payload["stalled"] = False
            payload["last_error"] = None
            claimed = _record(task_id, payload)
            return payload

        self._mutate(task_id, claim)
        return claimed

    async def _drive_turn(self, record: ACPTaskRecord, acp_config: Any) -> None:
        cwd = task_cwd(acp_config, record.cwd)
        runner = self._runner_factory(acp_config, cwd)
        active = _ActiveTurn(
            task_id=record.task_id,
            turn=record.turn,
            prompt=record.prompt,
            runner=runner,
            progress=dict(record.progress),
            tool_calls=list(record.tool_calls[-_TOOL_CALL_LIMIT:]),
            session_id=record.session_id,
        )
        self._active[record.task_id] = active
        try:
            current = self._get(record.task_id)
            if (
                current is None
                or current.status != ACPTaskStatus.WORKING.value
                or current.turn != record.turn
            ):
                return
            if current.cancel_requested:
                self._cancel_working_without_runner(record.task_id, record.turn)
                return
            result = await runner.run(
                record.prompt,
                cwd=cwd,
                run_id=record.task_id,
                session_id=record.session_id,
                on_progress=lambda update: self._on_progress(active, update),
            )
            await self._persist_progress(active, force=True)
            self._finalize_turn(active, result)
        finally:
            self._active.pop(record.task_id, None)
            latest = self._get(record.task_id)
            if latest is not None and latest.status == ACPTaskStatus.QUEUED.value:
                self.schedule(record.task_id)

    async def _on_progress(self, active: _ActiveTurn, update: dict[str, Any]) -> None:
        """Capture streamed ACP activity and persist it at a bounded cadence."""
        async with active.lock:
            kind = str(update.get("kind") or "")
            if kind == "session":
                session_id = update.get("session_id")
                if isinstance(session_id, str) and session_id:
                    active.session_id = session_id
            elif kind in {"message", "thought"}:
                text = update.get("text")
                if isinstance(text, str) and text:
                    active.progress["last_message"] = text[-4000:]
            elif kind == "plan" and isinstance(update.get("plan"), list):
                active.progress["plan"] = update["plan"]
            elif kind == "tool" and isinstance(update.get("tool_call"), dict):
                call = dict(update["tool_call"])
                active.tool_calls = [*active.tool_calls, call][-_TOOL_CALL_LIMIT:]
                if call.get("status") == "start":
                    active.progress["tool_call_count"] = (
                        int(active.progress.get("tool_call_count") or 0) + 1
                    )
                active.progress["last_tool"] = call.get("name") or call.get("kind")
            active.progress["last_activity_at"] = _now()
            active.dirty = True
            force = kind == "session" or active.stalled
        await self._persist_progress(active, force=force)

    async def _persist_progress(self, active: _ActiveTurn, *, force: bool) -> None:
        if not active.dirty:
            return
        if not force and time.monotonic() - active.last_persisted < self._progress_interval:
            return
        async with active.lock:
            if not active.dirty:
                return
            progress = dict(active.progress)
            tool_calls = list(active.tool_calls[-_TOOL_CALL_LIMIT:])
            session_id = active.session_id

            def persist(current: Any) -> Any:
                record = _record(active.task_id, current)
                if (
                    record is None
                    or record.status != ACPTaskStatus.WORKING.value
                    or record.turn != active.turn
                ):
                    return current
                payload = dict(current)
                payload["task_id"] = active.task_id
                payload["progress"] = progress
                payload["tool_calls"] = tool_calls
                if session_id:
                    payload["session_id"] = session_id
                payload["stalled"] = False
                payload["updated_at"] = _now()
                return payload

            self._mutate(active.task_id, persist)
            active.dirty = False
            active.stalled = False
            active.last_persisted = time.monotonic()

    def _finalize_turn(self, active: _ActiveTurn, result: Any) -> None:
        now = _now()
        summary = result.output.strip().splitlines()[0][:200] if result.output.strip() else None
        terminal_status = (
            ACPTaskStatus.INTERRUPTED.value
            if self._stopping
            else ACPTaskStatus.CANCELED.value
            if result.cancelled or result.stop_reason in {"cancelled", "canceled"}
            else ACPTaskStatus.COMPLETED.value
            if result.success
            else ACPTaskStatus.FAILED.value
        )
        history_entry = {
            "turn": active.turn,
            "status": terminal_status,
            "prompt": active.prompt,
            "output": result.output[-4000:] if result.output else "",
            "summary": summary,
            "stop_reason": result.stop_reason,
            "last_error": result.error,
            "completed_at": now,
        }

        def finalize(current: Any) -> Any:
            record = _record(active.task_id, current)
            if record is None:
                return current
            payload = dict(current)
            history = list(record.history)
            if not any(
                item.get("turn") == active.turn for item in history if isinstance(item, dict)
            ):
                history.append(history_entry)
            payload["history"] = history[-_HISTORY_LIMIT:]
            payload["task_id"] = active.task_id
            if record.status == ACPTaskStatus.QUEUED.value:
                # A newer request nudge owns the request fields and status.  Keep
                # it queued; the old turn is retained in history only.
                payload["updated_at"] = now
                return payload
            if record.status != ACPTaskStatus.WORKING.value or record.turn != active.turn:
                return payload
            terminal = (
                ACPTaskStatus.CANCELED.value
                if record.cancel_requested and not self._stopping
                else terminal_status
            )
            payload.update(
                {
                    "status": terminal,
                    "cancel_requested": False,
                    "session_id": result.session_id or active.session_id,
                    "output": result.output,
                    "summary": summary,
                    "stop_reason": result.stop_reason,
                    "last_error": result.error,
                    "tool_calls": list(result.tool_calls[-_TOOL_CALL_LIMIT:]),
                    "progress": dict(active.progress),
                    "stalled": False,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            return payload

        self._mutate(active.task_id, finalize)

    async def _cancel_requested(self, task_id: str) -> None:
        active = self._active.get(task_id)
        if active is not None:
            await active.runner.cancel(active.session_id)
            return

        def cancel(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None or not record.cancel_requested:
                return current
            payload = dict(current)
            if record.status == ACPTaskStatus.QUEUED.value:
                payload.update(
                    {
                        "task_id": task_id,
                        "status": ACPTaskStatus.CANCELED.value,
                        "cancel_requested": False,
                        "stop_reason": "cancelled",
                        "updated_at": _now(),
                        "completed_at": _now(),
                        "stalled": False,
                    }
                )
            return payload

        self._mutate(task_id, cancel)

    def _cancel_working_without_runner(self, task_id: str, turn: int) -> None:
        def cancel(current: Any) -> Any:
            record = _record(task_id, current)
            if (
                record is None
                or record.status != ACPTaskStatus.WORKING.value
                or record.turn != turn
                or not record.cancel_requested
            ):
                return current
            payload = dict(current)
            payload.update(
                {
                    "task_id": task_id,
                    "status": ACPTaskStatus.CANCELED.value,
                    "cancel_requested": False,
                    "stop_reason": "cancelled",
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            return payload

        self._mutate(task_id, cancel)

    def _fail_queued(self, task_id: str, error: str) -> None:
        def fail(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None or record.status != ACPTaskStatus.QUEUED.value:
                return current
            payload = dict(current)
            payload.update(
                {
                    "task_id": task_id,
                    "status": ACPTaskStatus.FAILED.value,
                    "cancel_requested": False,
                    "last_error": error,
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            return payload

        self._mutate(task_id, fail)

    def _mark_interrupted(self, task_id: str) -> None:
        def interrupt(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None or record.status != ACPTaskStatus.WORKING.value:
                return current
            payload = dict(current)
            payload.update(
                {
                    "task_id": task_id,
                    "status": ACPTaskStatus.INTERRUPTED.value,
                    "cancel_requested": False,
                    "stop_reason": "interrupted",
                    "last_error": "ACP backend restarted while this turn was working",
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            return payload

        self._mutate(task_id, interrupt)

    async def _watch_for_stalls(self) -> None:
        interval = min(self._progress_interval, max(1.0, self._stall_seconds / 10.0))
        while True:
            await asyncio.sleep(interval)
            now = utcnow()
            for active in list(self._active.values()):
                last_activity = active.progress.get("last_activity_at")
                try:
                    activity_at = datetime.fromisoformat(str(last_activity))
                except (TypeError, ValueError):
                    continue
                if (now - activity_at).total_seconds() < self._stall_seconds:
                    continue
                self._mark_stalled(active)

    def _mark_stalled(self, active: _ActiveTurn) -> None:
        def mark(current: Any) -> Any:
            record = _record(active.task_id, current)
            if (
                record is None
                or record.status != ACPTaskStatus.WORKING.value
                or record.turn != active.turn
                or record.stalled
            ):
                return current
            payload = dict(current)
            payload["task_id"] = active.task_id
            payload["stalled"] = True
            payload["updated_at"] = _now()
            return payload

        self._mutate(active.task_id, mark)
        active.stalled = True

    def _migrate_legacy_records(self) -> None:
        """Move the old list document without overwriting any newer task row."""
        store = self._store()
        legacy_document = store.get_document(PROGENITOR_STATE_PREFIX, "acp_tasks", None)
        legacy = legacy_document.payload if legacy_document is not None else None
        if not isinstance(legacy, list):
            return
        for item in legacy:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            key = f"{ACP_TASK_KEY_PREFIX}{task_id}"
            payload = dict(item)
            payload["task_id"] = task_id
            payload.setdefault("prompt", payload.pop("request_text", ""))
            try:
                normalized = ACPTaskRecord.model_validate(payload).model_dump(mode="json")
            except Exception:
                logger.warning("Skipping malformed legacy ACP task %s", task_id, exc_info=True)
                continue

            def create_if_absent(current: Any, initial: dict[str, Any] = normalized) -> Any:
                return current if current is not None else initial

            with document_write_origin(_SUPERVISOR_ORIGIN):
                store.mutate_document(PROGENITOR_STATE_PREFIX, key, create_if_absent)
        with document_write_origin(_SUPERVISOR_ORIGIN):
            store.delete_document(PROGENITOR_STATE_PREFIX, "acp_tasks")
