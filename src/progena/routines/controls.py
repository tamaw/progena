"""In-process cancellation control for native routine graph executions.

A routine run is cancellable while this process owns its native LangGraph
``Task``.  The durable control document records user intent and terminal
outcomes.  Every ``started`` lifecycle records the runtime process that owns
the execution, so a stop or cancel for a run started by an earlier runtime
process settles it directly: that process exited, so nothing is executing it,
and its last LangGraph checkpoint stays resumable.  Absence of a local task in
the owning process is never treated as a dead run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, cast

from ..runtime.instance import RUNTIME_INSTANCE_ID
from ..utils import utcnow_isoformat
from .events import persist_run_event

RoutineControlAction = Literal["stop", "cancel"]
_EXECUTING_STATUSES = frozenset({"started", "stopping", "cancelling"})
_PENDING_STATUSES = frozenset({"stopping", "cancelling"})
_TERMINAL_STATUSES = frozenset({"stopped", "cancelled", "completed", "failed"})


def routine_run_control_key(agent_id: str, session_id: str) -> str:
    """Return the shared backend/runtime durable-control document key."""
    return f"routine_run_control:{agent_id}:{session_id}"


def routine_session_from_thread(thread_id: str, agent_id: str | None = None) -> str:
    """Validate a routine thread id and return its logical session id."""
    parts = thread_id.split(":")
    if len(parts) != 4 or parts[0] != "routine" or not all(parts[1:]):
        raise ValueError(f"not a routine run thread_id: {thread_id!r}")
    if agent_id is not None and parts[1] != agent_id:
        raise ValueError("routine thread does not belong to the requested agent")
    return parts[3]


class RoutineRunControlConflict(RuntimeError):
    """The requested control has no matching active native execution."""


class RoutineRunCancelled(RoutineRunControlConflict):
    """An irreversible logical-run cancellation forbids any continuation."""


class RoutineRunControlExit(RuntimeError):
    """Handled exit from a graph task cancelled by an acknowledged control."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.status = str(payload["status"])
        super().__init__(f"routine run {self.status}")


@dataclass(frozen=True)
class RoutineExecutionScope:
    """The root user-owned task inherited by every nested routine invocation."""

    controller: RoutineRunController | None
    agent_id: str
    session_id: str
    thread_id: str
    request_id: str


_current_execution_scope: ContextVar[RoutineExecutionScope | None] = ContextVar(
    "routine_execution_scope", default=None
)


def current_routine_execution_scope() -> RoutineExecutionScope | None:
    """Return the root execution inherited by nested graph tasks, if any."""
    return _current_execution_scope.get()


@dataclass
class _ActiveExecution:
    scope: RoutineExecutionScope
    task: asyncio.Task[Any] | None = None
    intent: RoutineControlAction | None = None
    root_key: tuple[str, str, str] | None = None
    cancel_requested: bool = False


class RoutineRunController:
    """Own native graph tasks and translate user cancellation into lifecycle events.

    The registry is intentionally process-local: only a local task can be
    cancelled.  The paired durable document gives every entrypoint the same
    irreversible ``cancelled`` guard and records pending intent.  An outcome is
    written without a local task only for a run whose ``started`` lifecycle names
    an earlier runtime process.
    """

    def __init__(self, state_manager: Any) -> None:
        self._state_manager = state_manager
        self._active: dict[tuple[str, str, str], _ActiveExecution] = {}
        self._active_threads: dict[tuple[str, str], _ActiveExecution] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _identity(execution: dict[str, Any]) -> RoutineExecutionScope:
        agent_id = str(execution.get("agent_id") or "").strip()
        session_id = str(execution.get("session_id") or "").strip()
        thread_id = str(execution.get("thread_id") or "").strip()
        request_id = str(execution.get("request_id") or "").strip()
        if not all((agent_id, session_id, thread_id, request_id)):
            raise ValueError("routine execution is missing its control identity")
        if routine_session_from_thread(thread_id, agent_id) != session_id:
            raise ValueError("routine execution session does not match its thread_id")
        return RoutineExecutionScope(
            controller=None,
            agent_id=agent_id,
            session_id=session_id,
            thread_id=thread_id,
            request_id=request_id,
        )

    @staticmethod
    def _key(scope: RoutineExecutionScope) -> tuple[str, str, str]:
        return (scope.agent_id, scope.thread_id, scope.request_id)

    @staticmethod
    def _thread_key(scope: RoutineExecutionScope) -> tuple[str, str]:
        return (scope.agent_id, scope.thread_id)

    def owns_thread(self, agent_id: str, thread_id: str) -> bool:
        """Whether this process is executing a run on ``thread_id`` right now."""
        return self._active_threads.get((agent_id, thread_id)) is not None

    @staticmethod
    def _payload(scope: RoutineExecutionScope, status: str, **extra: Any) -> dict[str, Any]:
        return {
            "agent_id": scope.agent_id,
            "thread_id": scope.thread_id,
            "session_id": scope.session_id,
            "request_id": scope.request_id,
            "status": status,
            "timestamp": utcnow_isoformat(),
            **extra,
        }

    @staticmethod
    def _assert_control_admits(
        control: dict[str, Any] | None,
        scope: RoutineExecutionScope,
        *,
        resume_thread_id: str | None,
        allow_paused: bool,
    ) -> None:
        """Reject an execution its logical run's durable control state forbids."""
        if not control:
            return
        status = str(control.get("status") or "")
        if status == "cancelled":
            raise RoutineRunCancelled("routine run was cancelled and cannot be resumed")
        if status == "stopped":
            if resume_thread_id != scope.thread_id:
                raise RoutineRunControlConflict(
                    "stopped routine runs must resume their exact checkpoint thread"
                )
        elif status == "resuming":
            # The backend claimed the stopped checkpoint for one queued request.
            if resume_thread_id != scope.thread_id or control.get("request_id") != scope.request_id:
                raise RoutineRunControlConflict(
                    "stopped routine run is already claimed by another resume"
                )
        elif status == "paused":
            if not allow_paused:
                raise RoutineRunControlConflict(
                    "paused routine run requires its pending continuation path"
                )
        elif status in _PENDING_STATUSES or status == "running":
            raise RoutineRunControlConflict("routine run is already active")
        elif status in _TERMINAL_STATUSES:
            raise RoutineRunControlConflict("routine run is already terminal")

    async def release_resume_claim(self, execution: dict[str, Any]) -> None:
        """Return a claimed stopped checkpoint when its resume failed before starting."""
        agent_id = str(execution.get("agent_id") or "")
        session_id = str(execution.get("session_id") or "")
        if not agent_id or not session_id:
            return
        async with self._lock:
            control = await self._load_control(agent_id, session_id)
            stopped = control.get("resumed_from") if control else None
            if (
                control is not None
                and control.get("status") == "resuming"
                and control.get("request_id") == execution.get("request_id")
                and isinstance(stopped, dict)
            ):
                await self._save_control(stopped)

    async def _load_control(self, agent_id: str, session_id: str) -> dict[str, Any] | None:
        reader = getattr(self._state_manager, "get_routine_run_control", None)
        if not callable(reader):
            return None
        load = cast(Callable[[str, str], Awaitable[object]], reader)
        control = await load(agent_id, session_id)
        return dict(control) if isinstance(control, dict) else None

    async def _save_control(self, payload: dict[str, Any]) -> None:
        writer = getattr(self._state_manager, "save_routine_run_control", None)
        if callable(writer):
            await cast(Callable[[dict[str, Any]], Awaitable[None]], writer)(payload)

    async def _load_run_events(self, request_id: str) -> list[dict[str, Any]]:
        reader = getattr(self._state_manager, "list_routine_run_events", None)
        if not callable(reader):
            return []
        load = cast(Callable[[str], Awaitable[list[dict[str, Any]]]], reader)
        return [dict(event) for event in await load(request_id) if isinstance(event, dict)]

    async def record_started(self, execution: dict[str, Any]) -> None:
        """Persist ``started`` naming this process as the execution's owner."""
        execution["runtime_instance"] = RUNTIME_INSTANCE_ID
        await persist_run_event(self._state_manager, execution, "started")

    async def _settle_previous_process_run(
        self, scope: RoutineExecutionScope, action: RoutineControlAction
    ) -> dict[str, Any]:
        """Settle a run whose owning runtime process has exited (caller holds the lock).

        Only a run still executing according to its lifecycle and started by a
        different runtime process qualifies; ``started`` events written before
        ownership was recorded predate every process that records it.
        """
        if self._active_threads.get(self._thread_key(scope)) is not None:
            raise RoutineRunControlConflict("no matching active routine run")
        events = [
            event
            for event in await self._load_run_events(scope.request_id)
            if event.get("agent_id") == scope.agent_id and event.get("thread_id") == scope.thread_id
        ]
        latest = max(events, key=lambda event: str(event.get("timestamp") or ""), default=None)
        if latest is None or latest.get("status") not in _EXECUTING_STATUSES:
            raise RoutineRunControlConflict("no matching active routine run")
        started = next((event for event in events if event.get("status") == "started"), None)
        if started is None or started.get("runtime_instance") == RUNTIME_INSTANCE_ID:
            raise RoutineRunControlConflict("no matching active routine run")
        control = await self._load_control(scope.agent_id, scope.session_id)
        if control and control.get("request_id") != scope.request_id:
            raise RoutineRunControlConflict("routine run session belongs to another attempt")
        cancel = (
            action == "cancel"
            or latest.get("status") == "cancelling"
            or (control or {}).get("status") in {"cancelling", "cancelled"}
        )
        status = "cancelled" if cancel else "stopped"
        payload = self._payload(scope, status)
        await self._save_control(payload)
        await persist_run_event(self._state_manager, payload, status)
        return payload

    async def assert_not_cancelled(self, agent_id: str, session_id: str) -> None:
        """Reject every continuation path after logical-run cancellation."""
        control = await self._load_control(agent_id, session_id)
        if control and control.get("status") == "cancelled":
            raise RoutineRunCancelled("routine run was cancelled and cannot be resumed")

    async def assert_startable(
        self,
        execution: dict[str, Any],
        *,
        resume_thread_id: str | None = None,
        allow_paused: bool = False,
    ) -> None:
        """Reject a known non-continuable logical run before emitting ``started``."""
        raw_scope = self._identity(execution)
        scope = RoutineExecutionScope(
            controller=self,
            agent_id=raw_scope.agent_id,
            session_id=raw_scope.session_id,
            thread_id=raw_scope.thread_id,
            request_id=raw_scope.request_id,
        )
        async with self._lock:
            if (
                self._active.get(self._key(scope)) is not None
                or self._active_threads.get(self._thread_key(scope)) is not None
            ):
                raise RoutineRunControlConflict("routine run is already active")
            control = await self._load_control(scope.agent_id, scope.session_id)
            self._assert_control_admits(
                control, scope, resume_thread_id=resume_thread_id, allow_paused=allow_paused
            )

    async def register_nested_execution(
        self,
        execution: dict[str, Any],
        *,
        resume_thread_id: str | None = None,
        allow_paused: bool = False,
    ) -> None:
        """Make a visible nested lifecycle cancellable through its root task.

        Native LangGraph executes nested routines in the root graph task.  A
        child therefore cannot own a second task safely; its exact UI identity
        is registered against that root task, and a child control escalates to
        unwind the root.  Terminal child events are emitted only after that
        root task has actually ended.
        """
        inherited = current_routine_execution_scope()
        if inherited is None or inherited.controller is None:
            raise RoutineRunControlConflict("nested routine has no active root execution")
        if inherited.controller is not self:
            await inherited.controller.register_nested_execution(
                execution,
                resume_thread_id=resume_thread_id,
                allow_paused=allow_paused,
            )
            return
        raw_scope = self._identity(execution)
        scope = RoutineExecutionScope(
            controller=self,
            agent_id=raw_scope.agent_id,
            session_id=raw_scope.session_id,
            thread_id=raw_scope.thread_id,
            request_id=raw_scope.request_id,
        )
        key = self._key(scope)
        root_key = self._key(inherited)
        async with self._lock:
            root = self._active.get(root_key)
            if root is None or root.task is None or root.task.done():
                raise RoutineRunControlConflict("nested routine has no active root execution")
            if (
                self._active.get(key) is not None
                or self._active_threads.get(self._thread_key(scope)) is not None
            ):
                raise RoutineRunControlConflict("routine run is already active")
            control = await self._load_control(scope.agent_id, scope.session_id)
            self._assert_control_admits(
                control, scope, resume_thread_id=resume_thread_id, allow_paused=allow_paused
            )
            entry = _ActiveExecution(scope=scope, task=root.task, root_key=root_key)
            self._active[key] = entry
            self._active_threads[self._thread_key(scope)] = entry
            await self._save_control(self._payload(scope, "running"))

    async def run_graph_execution(
        self,
        execution: dict[str, Any],
        operation: Coroutine[Any, Any, dict[str, Any]],
        *,
        resume_thread_id: str | None = None,
        allow_paused: bool = False,
    ) -> dict[str, Any]:
        """Run one root graph in its own task and own its cancellation lifecycle.

        A nested invocation inherits this scope in the task context and must not
        create another independently controllable task.  Only this method turns
        a cancellation into :class:`RoutineRunControlExit`; shutdown cancellation
        remains a real ``CancelledError``.
        """
        raw_scope = self._identity(execution)
        scope = RoutineExecutionScope(
            controller=self,
            agent_id=raw_scope.agent_id,
            session_id=raw_scope.session_id,
            thread_id=raw_scope.thread_id,
            request_id=raw_scope.request_id,
        )
        key = self._key(scope)
        entry: _ActiveExecution | None = None
        try:
            async with self._lock:
                existing = self._active.get(key)
                thread_entry = self._active_threads.get(self._thread_key(scope))
                if existing is not None or thread_entry is not None:
                    raise RoutineRunControlConflict("routine run is already active")
                control = await self._load_control(scope.agent_id, scope.session_id)
                self._assert_control_admits(
                    control, scope, resume_thread_id=resume_thread_id, allow_paused=allow_paused
                )
                entry = _ActiveExecution(scope=scope, root_key=key)
                self._active[key] = entry
                self._active_threads[self._thread_key(scope)] = entry
                await self._save_control(self._payload(scope, "running"))
        except BaseException:
            if entry is not None:
                await self._discard(entry)
            close = getattr(operation, "close", None)
            if callable(close):
                close()
            raise

        token = _current_execution_scope.set(scope)
        try:
            task = asyncio.create_task(operation)
        finally:
            _current_execution_scope.reset(token)
        # There is no await between task creation and publishing the task, so an
        # HTTP control cannot observe a live reservation with no cancellable task.
        assert entry is not None
        entry.task = task
        try:
            return await task
        except asyncio.CancelledError:
            if entry.intent is None:
                await self._discard(entry)
                raise
            payload = await self._settle_controlled(entry)
            raise RoutineRunControlExit(payload) from None
        except RoutineRunControlExit:
            if entry.intent is not None:
                payload = await self._settle_controlled(entry)
                raise RoutineRunControlExit(payload) from None
            await self._discard(entry)
            raise

    async def complete_execution(self, execution: dict[str, Any], status: str) -> None:
        """Close normal task ownership after its caller has selected its outcome."""
        raw_scope = self._identity(execution)
        key = (raw_scope.agent_id, raw_scope.thread_id, raw_scope.request_id)
        async with self._lock:
            entry = self._active.get(key)
            if entry is None:
                return
            # A task that suppressed cancellation has really finished normally;
            # record its actual caller-selected outcome, never a fabricated stop.
            self._active.pop(key, None)
            if self._active_threads.get(self._thread_key(entry.scope)) is entry:
                self._active_threads.pop(self._thread_key(entry.scope), None)
            await self._save_control(self._payload(entry.scope, status))

    async def fail_execution(self, execution: dict[str, Any]) -> None:
        """Release ownership for a real graph error without masking that error."""
        await self.complete_execution(execution, "failed")

    async def _discard(self, entry: _ActiveExecution) -> None:
        """Forget shutdown-owned tasks without writing a user-control outcome."""
        async with self._lock:
            root_key = entry.root_key or self._key(entry.scope)
            affected = [
                candidate for candidate in self._active.values() if candidate.root_key == root_key
            ]
            for candidate in affected:
                key = self._key(candidate.scope)
                if self._active.get(key) is candidate:
                    self._active.pop(key, None)
                if self._active_threads.get(self._thread_key(candidate.scope)) is candidate:
                    self._active_threads.pop(self._thread_key(candidate.scope), None)

    async def _settle_controlled(self, entry: _ActiveExecution) -> dict[str, Any]:
        """Persist every affected terminal lifecycle after root-task unwinding."""
        assert entry.intent is not None
        root_key = entry.root_key or self._key(entry.scope)
        payloads: list[tuple[dict[str, Any], str]] = []
        async with self._lock:
            root = self._active.get(root_key) or entry
            root_intent = root.intent or entry.intent
            affected = [
                candidate for candidate in self._active.values() if candidate.root_key == root_key
            ]
            for candidate in affected:
                status = (
                    "cancelled"
                    if root_intent == "cancel" or candidate.intent == "cancel"
                    else "stopped"
                )
                key = self._key(candidate.scope)
                if self._active.get(key) is candidate:
                    self._active.pop(key, None)
                if self._active_threads.get(self._thread_key(candidate.scope)) is candidate:
                    self._active_threads.pop(self._thread_key(candidate.scope), None)
                payload = self._payload(candidate.scope, status)
                await self._save_control(payload)
                payloads.append((payload, status))
        for payload, status in payloads:
            await persist_run_event(self._state_manager, payload, status)
        target_key = self._key(entry.scope)
        return next(
            payload
            for payload, _status in payloads
            if (
                str(payload["agent_id"]),
                str(payload["thread_id"]),
                str(payload["request_id"]),
            )
            == target_key
        )

    async def control_routine_run(
        self,
        agent_id: str,
        *,
        thread_id: str,
        request_id: str,
        action: RoutineControlAction,
    ) -> dict[str, Any]:
        """Acknowledge a requested stop/cancel without waiting for termination."""
        agent_id = str(agent_id or "").strip()
        thread_id = str(thread_id or "").strip()
        request_id = str(request_id or "").strip()
        if action not in {"stop", "cancel"}:
            raise ValueError("action must be 'stop' or 'cancel'")
        if not all((agent_id, thread_id, request_id)):
            raise ValueError("agent_id, thread_id, and request_id are required")
        try:
            session_id = routine_session_from_thread(thread_id, agent_id)
        except ValueError as exc:
            raise RoutineRunControlConflict(str(exc)) from exc
        key = (agent_id, thread_id, request_id)
        async with self._lock:
            entry = self._active.get(key)
            if entry is None:
                return await self._settle_previous_process_run(
                    RoutineExecutionScope(
                        controller=self,
                        agent_id=agent_id,
                        session_id=session_id,
                        thread_id=thread_id,
                        request_id=request_id,
                    ),
                    action,
                )
            if entry.task is None or entry.task.done():
                raise RoutineRunControlConflict("no matching active routine run")
            if entry.scope.session_id != session_id:
                raise RoutineRunControlConflict(
                    "routine control identity does not match its session"
                )
            root = self._active.get(entry.root_key or key) or entry
            if entry.intent == "cancel" or root.intent == "cancel" or action == "cancel":
                entry.intent = "cancel"
                root.intent = "cancel"
                status = "cancelling"
            else:
                entry.intent = "stop"
                root.intent = "stop"
                status = "stopping"
            payload = self._payload(entry.scope, status)
            root_key = root.root_key or self._key(root.scope)
            for candidate in self._active.values():
                if candidate.root_key == root_key:
                    pending = self._payload(candidate.scope, status)
                    await self._save_control(pending)
                    # Pending intent must survive a browser reconnect too.
                    # It is not a terminal acknowledgement of execution.
                    await persist_run_event(self._state_manager, pending, status)
            # Task.cancel only requests delivery at the next await.  Returning
            # here is intentionally an acknowledgement, not a termination claim.
            # Cancel the shared root task once: a second cancel would interrupt
            # its native teardown; later controls only update durable intent.
            if not root.cancel_requested:
                if not entry.task.cancel():
                    raise RoutineRunControlConflict("no matching active routine run")
                root.cancel_requested = True
            return payload
