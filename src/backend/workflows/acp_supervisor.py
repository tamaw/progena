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

from src.backend.events.documents import TypedDocumentEvent
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    document_write_origin,
)
from src.progena.acp.models import ACPFailureKind, ACPTaskRecord, ACPTaskStatus
from src.progena.acp.runner import ACPRunner
from src.progena.acp.tasks import (
    acp_config_from_agent_config,
    acp_failure_kind,
    runner_for,
    task_command,
    task_cwd,
)
from src.progena.documents.kinds import ACP_TASK_KEY_PREFIX
from src.progena.utils import utcnow

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


def _failure_kind(status: str, error: str | None) -> ACPFailureKind | None:
    """Classify only failed turns; every other terminal status has no failure kind."""
    return acp_failure_kind(error) if status == ACPTaskStatus.FAILED.value else None


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
    document = store.get_document(PROGENA_STATE_PREFIX, "registry", {})
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
    mode: str | None
    input_id: str | None = None
    input_future: asyncio.Future[Any] | None = None
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
        self._max_sessions = _positive_int("PROGENA_ACP_MAX_SESSIONS", 2)
        self._max_turns = _positive_int("PROGENA_ACP_MAX_TURNS", 20)
        self._progress_interval = _positive_float("PROGENA_ACP_PROGRESS_INTERVAL_SECONDS", 15.0)
        self._stall_seconds = _positive_float("PROGENA_ACP_STALL_SECONDS", 600.0)
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
                PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", mutator
            )

    def _get(self, task_id: str) -> ACPTaskRecord | None:
        document = self._store().get_document(
            PROGENA_STATE_PREFIX, f"{ACP_TASK_KEY_PREFIX}{task_id}", None
        )
        return _record(task_id, document.payload if document is not None else None)

    async def start(self) -> None:
        """Migrate legacy records, recover interrupted work, and queue pending tasks."""
        if self._started:
            return
        self._started = True
        self._stopping = False
        self._migrate_legacy_records()
        rows = self._store().list_documents(PROGENA_STATE_PREFIX, ACP_TASK_KEY_PREFIX)
        for key, document in rows:
            task_id = _task_id_from_key(key)
            if task_id is None:
                continue
            record = _record(task_id, document.payload)
            if record is None:
                continue
            if record.status == ACPTaskStatus.WORKING.value:
                self._mark_interrupted(task_id)
                recovered = self._get(task_id)
                if recovered is not None and self._has_pending_message(recovered):
                    self._queue_pending_message_turn(task_id)
                continue
            if record.pending_input is not None:
                self._clear_pending_input(task_id)
                record = self._get(task_id)
                if record is None:
                    continue
            if record.status == ACPTaskStatus.QUEUED.value:
                self.schedule(task_id)
            elif self._has_pending_message(record):
                self._queue_pending_message_turn(task_id)
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
            self._resolve_input(active, {"action": "decline"})
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
                self._resolve_input(active, {"action": "decline"})
                await active.runner.cancel(active.session_id)
            return
        record = _record(task_id, typed.event.after)
        if record is None:
            return
        if record.cancel_requested:
            await self._cancel_requested(task_id)
            return
        active = self._active.get(task_id)
        if active is not None:
            if (
                record.mode
                and record.mode != active.mode
                and await active.runner.set_mode(record.mode)
            ):
                active.mode = record.mode
            await self._deliver_input(active)
            await self._process_pending_messages(active)
            if record.status == ACPTaskStatus.QUEUED.value:
                await active.runner.cancel(active.session_id)
            return
        if record.status == ACPTaskStatus.QUEUED.value:
            self.schedule(task_id)
        elif self._has_pending_message(record):
            self._queue_pending_message_turn(task_id)
        elif record.pending_input is None and record.answers:
            self._clear_stale_answers(task_id)

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
                    f"ACP task exceeded PROGENA_ACP_MAX_TURNS ({self._max_turns})",
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
                        f"ACP task exceeded PROGENA_ACP_MAX_TURNS ({self._max_turns})",
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
            payload["failure_kind"] = None
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
            mode=record.mode,
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
            active.mode = current.mode
            result = await runner.run(
                record.prompt,
                cwd=cwd,
                run_id=record.task_id,
                session_id=record.session_id,
                mode=active.mode,
                on_progress=lambda update: self._on_progress(active, update),
                on_input=lambda request: self._on_input(active, request),
            )
            await self._persist_progress(active, force=True)
            self._finalize_turn(active, result)
        finally:
            self._resolve_input(active, {"action": "decline"})
            self._active.pop(record.task_id, None)
            latest = self._get(record.task_id)
            if latest is not None and latest.status != ACPTaskStatus.WORKING.value:
                self._queue_pending_message_turn(record.task_id)
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
        if kind == "session":
            await self._process_pending_messages(active)

    @staticmethod
    def _has_pending_message(record: ACPTaskRecord) -> bool:
        return any(
            isinstance(message, dict) and message.get("status") == "pending"
            for message in record.messages
        )

    @staticmethod
    def _first_pending_message(record: ACPTaskRecord) -> dict[str, Any] | None:
        return next(
            (
                message
                for message in record.messages
                if isinstance(message, dict)
                and message.get("status") == "pending"
                and isinstance(message.get("id"), str)
                and isinstance(message.get("text"), str)
            ),
            None,
        )

    async def _process_pending_messages(self, active: _ActiveTurn) -> None:
        """Steer every queued message into the currently running ACP turn."""
        if not active.session_id:
            return
        while True:
            record = self._get(active.task_id)
            if (
                record is None
                or record.status != ACPTaskStatus.WORKING.value
                or record.turn != active.turn
            ):
                return
            message = self._first_pending_message(record)
            if message is None:
                return
            message_id = message["id"]
            try:
                outcome = await active.runner.steer(active.session_id, message["text"])
            except Exception as exc:
                logger.warning("ACP steering failed for task %s", active.task_id, exc_info=True)
                self._update_message(
                    active.task_id,
                    message_id,
                    status="failed",
                    error=str(exc),
                )
                continue
            if outcome == "injected":
                self._update_message(active.task_id, message_id, status="injected")
                continue
            if outcome == "promptRequired":
                return
            self._update_message(
                active.task_id,
                message_id,
                status="failed",
                error=f"ACP steering returned {outcome}",
            )

    def _update_message(
        self,
        task_id: str,
        message_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        def update(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None:
                return current
            messages = list(record.messages)
            for index, message in enumerate(messages):
                if not isinstance(message, dict) or message.get("id") != message_id:
                    continue
                if message.get("status") != "pending":
                    return current
                updated = dict(message)
                updated["status"] = status
                updated["sent_at"] = _now()
                if error:
                    updated["error"] = error
                else:
                    updated.pop("error", None)
                messages[index] = updated
                payload = dict(current)
                payload["messages"] = messages
                payload["updated_at"] = _now()
                return payload
            return current

        self._mutate(task_id, update)

    def _queue_pending_message_turn(self, task_id: str) -> None:
        """Turn the first pending idle message into the task's next prompt."""
        queued = False

        def queue(current: Any) -> Any:
            nonlocal queued
            record = _record(task_id, current)
            if record is None or record.status in {
                ACPTaskStatus.QUEUED.value,
                ACPTaskStatus.WORKING.value,
            }:
                return current
            message = self._first_pending_message(record)
            if message is None:
                return current
            messages = list(record.messages)
            for index, candidate in enumerate(messages):
                if isinstance(candidate, dict) and candidate.get("id") == message["id"]:
                    updated = dict(candidate)
                    updated["status"] = "started_turn"
                    updated["sent_at"] = _now()
                    updated.pop("error", None)
                    messages[index] = updated
                    payload = dict(current)
                    payload.update(
                        {
                            "messages": messages,
                            "prompt": message["text"],
                            "status": ACPTaskStatus.QUEUED.value,
                            "cancel_requested": False,
                            "updated_at": _now(),
                            "completed_at": None,
                            "stalled": False,
                        }
                    )
                    queued = True
                    return payload
            return current

        self._mutate(task_id, queue)
        if queued:
            self.schedule(task_id)

    async def _on_input(self, active: _ActiveTurn, request: dict[str, Any]) -> Any:
        """Publish an ACP input request and wait for its matching document answer."""
        input_id = str(request.get("id") or "")
        kind = str(request.get("kind") or "")
        if not input_id or kind not in {"question", "plan", "permission"}:
            return {"action": "decline"}
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        active.input_id = input_id
        active.input_future = future
        pending = {
            "id": input_id,
            "kind": kind,
            "message": str(request.get("message") or ""),
            "requested_at": _now(),
        }
        for key in ("fields", "plan", "tool", "options"):
            if key in request:
                pending[key] = request[key]
        if not self._publish_pending_input(active, pending):
            self._resolve_input(active, {"action": "decline"})
        else:
            await self._deliver_input(active)
        try:
            return await future
        finally:
            if active.input_future is future:
                active.input_future = None
                active.input_id = None

    def _publish_pending_input(self, active: _ActiveTurn, pending: dict[str, Any]) -> bool:
        published = False

        def publish(current: Any) -> Any:
            nonlocal published
            record = _record(active.task_id, current)
            if (
                record is None
                or record.status != ACPTaskStatus.WORKING.value
                or record.turn != active.turn
            ):
                return current
            payload = dict(current)
            payload["pending_input"] = pending
            payload["updated_at"] = _now()
            published = True
            return payload

        self._mutate(active.task_id, publish)
        return published

    async def _deliver_input(self, active: _ActiveTurn) -> None:
        input_id = active.input_id
        if not input_id:
            return
        found, answer = self._take_answer(active, input_id)
        if found:
            self._resolve_input(active, answer)

    def _take_answer(self, active: _ActiveTurn, input_id: str) -> tuple[bool, Any]:
        found = False
        answer: Any = None

        def take(current: Any) -> Any:
            nonlocal found, answer
            record = _record(active.task_id, current)
            if (
                record is None
                or record.status != ACPTaskStatus.WORKING.value
                or record.turn != active.turn
                or not isinstance(record.pending_input, dict)
                or record.pending_input.get("id") != input_id
                or input_id not in record.answers
            ):
                return current
            answers = dict(record.answers)
            answer = answers.pop(input_id)
            payload = dict(current)
            payload["answers"] = answers
            payload["pending_input"] = None
            payload["updated_at"] = _now()
            found = True
            return payload

        self._mutate(active.task_id, take)
        return found, answer

    @staticmethod
    def _resolve_input(active: _ActiveTurn, answer: Any) -> None:
        future = active.input_future
        if future is not None and not future.done():
            future.set_result(answer)

    def _clear_pending_input(self, task_id: str, turn: int | None = None) -> None:
        def clear(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None or (turn is not None and record.turn != turn):
                return current
            input_id = (
                record.pending_input.get("id") if isinstance(record.pending_input, dict) else None
            )
            if record.pending_input is None and not input_id:
                return current
            payload = dict(current)
            payload["pending_input"] = None
            if isinstance(input_id, str):
                answers = dict(record.answers)
                answers.pop(input_id, None)
                payload["answers"] = answers
            payload["updated_at"] = _now()
            return payload

        self._mutate(task_id, clear)

    def _clear_stale_answers(self, task_id: str) -> None:
        def clear(current: Any) -> Any:
            record = _record(task_id, current)
            if record is None or record.status == ACPTaskStatus.WORKING.value or not record.answers:
                return current
            payload = dict(current)
            payload["answers"] = {}
            payload["pending_input"] = None
            payload["updated_at"] = _now()
            return payload

        self._mutate(task_id, clear)

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
            "failure_kind": _failure_kind(terminal_status, result.error),
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
            pending_id = (
                record.pending_input.get("id") if isinstance(record.pending_input, dict) else None
            )
            if pending_id:
                answers = dict(record.answers)
                answers.pop(str(pending_id), None)
                payload["answers"] = answers
            payload["pending_input"] = None
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
                    "failure_kind": _failure_kind(terminal, result.error),
                    "tool_calls": list(result.tool_calls[-_TOOL_CALL_LIMIT:]),
                    "progress": dict(active.progress),
                    "stalled": False,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            return payload

        self._mutate(active.task_id, finalize)

    @staticmethod
    def _clear_pending_answer(record: ACPTaskRecord, payload: dict[str, Any]) -> None:
        """Clear a terminal input request and its answer without touching other answers."""
        pending_id = (
            record.pending_input.get("id") if isinstance(record.pending_input, dict) else None
        )
        payload["pending_input"] = None
        if isinstance(pending_id, str):
            answers = dict(record.answers)
            answers.pop(pending_id, None)
            payload["answers"] = answers

    async def _cancel_requested(self, task_id: str) -> None:
        active = self._active.get(task_id)
        if active is not None:
            self._resolve_input(active, {"action": "decline"})
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
                        "pending_input": None,
                        "stop_reason": "cancelled",
                        "updated_at": _now(),
                        "completed_at": _now(),
                        "stalled": False,
                    }
                )
                self._clear_pending_answer(record, payload)
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
                    "pending_input": None,
                    "stop_reason": "cancelled",
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            self._clear_pending_answer(record, payload)
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
                    "pending_input": None,
                    "last_error": error,
                    "failure_kind": acp_failure_kind(error),
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            self._clear_pending_answer(record, payload)
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
                    "pending_input": None,
                    "stop_reason": "interrupted",
                    "last_error": "ACP backend restarted while this turn was working",
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "stalled": False,
                }
            )
            self._clear_pending_answer(record, payload)
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
        legacy_document = store.get_document(PROGENA_STATE_PREFIX, "acp_tasks", None)
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
                store.mutate_document(PROGENA_STATE_PREFIX, key, create_if_absent)
        with document_write_origin(_SUPERVISOR_ORIGIN):
            store.delete_document(PROGENA_STATE_PREFIX, "acp_tasks")
