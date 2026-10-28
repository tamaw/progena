"""Routine notifications: global lifecycle, connection-scoped checkpoint detail."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any

import src.backend.events.stream as events_stream
from src.backend.integrations.runtime import get_thread_state
from src.backend.runtime_liveness import live_runtime, owner_is_gone
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    DocumentEvent,
    StoreChangeListener,
    store_database_url,
)
from src.backend.storage.documents import get_default_document_store as get_document_store
from src.progena.routines.approvals import decision_key, pending_approvals
from src.progena.routines.events import CHECKPOINT_CHANNEL, RUN_EVENT_PREFIX

_EXECUTING_LIFECYCLE_STATUSES = frozenset({"started", "stopping", "cancelling"})


def _lifecycle_snapshot(
    payload: dict[str, Any], started: dict[str, Any] | None, *, live
) -> dict[str, Any]:
    """Overlay a read-only interruption status without altering persisted events."""
    if payload.get("status") not in _EXECUTING_LIFECYCLE_STATUSES or started is None:
        return payload
    owner = started.get("runtime_instance")
    owner_instance = owner if isinstance(owner, str) else None
    started_at = started.get("timestamp")
    if not isinstance(started_at, str):
        started_at = None
    if not owner_is_gone(owner_instance, started_at, live):
        return payload
    return {
        **payload,
        **({"inputs": started["inputs"]} if "inputs" in started else {}),
        "status": "interrupted",
        "interrupted_at": live.started_at,
        "started_at": started_at,
    }


logger = logging.getLogger(__name__)

_RETAINED_LIFECYCLE_STATUSES = frozenset(
    {"enqueued", "started", "paused", "stopping", "cancelling", "stopped"}
)


async def read_run_state(agent_id: str, thread_id: str, doc_store=None) -> dict[str, Any]:
    """Reconstruct checkpoint blob channels using LangGraph's native saver."""
    payload = {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "found": False,
        "run_meta": {},
        "data": {},
        "pending_approvals": [],
    }
    doc_store = doc_store if doc_store is not None else get_document_store()
    database_url = store_database_url(doc_store)
    if database_url:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
            if checkpoint is not None:
                values = checkpoint.checkpoint.get("channel_values", {})
                payload.update(
                    found=True, run_meta=values.get("run_meta") or {}, data=values.get("data") or {}
                )
                interrupts = [
                    item
                    for _, channel, value in (checkpoint.pending_writes or [])
                    if channel == "__interrupt__"
                    for item in value
                ]
                approvals = pending_approvals(interrupts)
                for approval in approvals:
                    claim = await asyncio.to_thread(
                        doc_store.get_document,
                        PROGENA_STATE_PREFIX,
                        decision_key(thread_id, approval["interrupt_id"]),
                    )
                    approval["answer_pending"] = claim is not None
                payload["pending_approvals"] = approvals
    return payload


async def routine_lifecycles_by_thread(agent_id: str, store=None) -> dict[str, dict[str, Any]]:
    """Return latest lifecycle snapshots keyed by routine checkpoint thread."""
    store = store if store is not None else get_document_store()
    documents = await asyncio.to_thread(
        store.list_documents, PROGENA_STATE_PREFIX, RUN_EVENT_PREFIX
    )
    latest: dict[str, dict[str, Any]] = {}
    started: dict[str, dict[str, Any]] = {}
    for _, document in documents:
        payload = document.payload
        if not isinstance(payload, dict) or payload.get("agent_id") != agent_id:
            continue
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            continue
        if payload.get("status") == "started":
            started[request_id] = payload
        if payload.get("timestamp", "") >= latest.get(request_id, {}).get("timestamp", ""):
            latest[request_id] = payload
    live = live_runtime(store)
    by_thread: dict[str, dict[str, Any]] = {}
    for request_id, payload in latest.items():
        thread_id = payload.get("thread_id")
        if not isinstance(thread_id, str):
            continue
        # A resumed thread has several attempts; the newest one describes it.
        current = by_thread.get(thread_id)
        if current is None or payload.get("timestamp", "") >= current.get("timestamp", ""):
            by_thread[thread_id] = _lifecycle_snapshot(payload, started.get(request_id), live=live)
    return by_thread


class RoutineEvents:
    def __init__(self) -> None:
        self.interests: dict[str, dict[str, Any]] = {}
        self.connections: set[str] = set()
        self.listener: StoreChangeListener | None = None
        self.tasks: set[asyncio.Task] = set()
        self.pending: dict[str, asyncio.Task] = {}
        self.dirty: set[str] = set()
        self.delivered: OrderedDict[tuple[str, str], None] = OrderedDict()

    def claim_lifecycle(self, payload: dict[str, Any]) -> bool:
        key = (payload["request_id"], payload["status"])
        if key in self.delivered:
            return False
        self.delivered[key] = None
        if len(self.delivered) > 10000:
            self.delivered.popitem(last=False)
        return True

    def spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(self._report_error)
        return task

    @staticmethod
    def _report_error(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.error("Routine event delivery failed", exc_info=task.exception())

    async def start(self) -> None:
        database_url = store_database_url(get_document_store())
        if database_url and self.listener is None:
            self.listener = StoreChangeListener(
                database_url,
                channel=CHECKPOINT_CHANNEL,
                on_change=self.checkpoint_changed,
                on_resync=self._schedule_reconcile,
            )
            await self.listener.start()

    def _schedule_reconcile(self) -> None:
        self.spawn(self.reconcile())

    async def stop(self) -> None:
        if self.listener:
            await self.listener.stop()
            self.listener = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.interests.clear()
        self.connections.clear()
        self.pending.clear()
        self.dirty.clear()

    def disconnected(self, client_id: str) -> None:
        self.connections.discard(client_id)
        self.interests.pop(client_id, None)

    async def document_event(self, event: DocumentEvent) -> None:
        """Publish a routine run lifecycle transition recorded in the document event log."""
        if event.key.startswith(RUN_EVENT_PREFIX) and isinstance(event.after, dict):
            self.publish_lifecycle(event.after)

    def publish_lifecycle(self, payload: dict[str, Any]) -> None:

        if self.claim_lifecycle(payload):
            events_stream.broadcast_ui_event(f"agent.routine.run.{payload['status']}", payload)
        agent_id = payload["agent_id"]
        if any(i["agent_id"] == agent_id for i in self.interests.values()):
            self.schedule(agent_id)

    def checkpoint_changed(self, notification: str) -> None:
        try:
            thread_id = json.loads(notification)["thread_id"]
            parts = thread_id.split(":")
            if len(parts) == 4 and parts[0] == "routine":
                self.schedule(parts[1])
        except (ValueError, KeyError, TypeError):
            logger.warning("Invalid routine checkpoint notification")

    def schedule(self, agent_id: str) -> None:
        if not any(i["agent_id"] == agent_id for i in self.interests.values()):
            return
        self.dirty.add(agent_id)
        if agent_id not in self.pending:
            self.pending[agent_id] = self.spawn(self._drain(agent_id))

    async def _drain(self, agent_id: str) -> None:
        try:
            while agent_id in self.dirty:
                self.dirty.discard(agent_id)
                await self.refresh(agent_id)
        finally:
            self.pending.pop(agent_id, None)

    async def refresh(self, agent_id: str) -> None:
        from dataclasses import asdict

        watchers = {c: i for c, i in self.interests.items() if i["agent_id"] == agent_id}
        if not watchers:
            return
        store = get_document_store()
        runs, has_more = await asyncio.to_thread(store.list_routine_runs, agent_id)
        entries = [asdict(run) for run in runs]
        lifecycle_by_thread = await routine_lifecycles_by_thread(agent_id, store)
        for lifecycle in lifecycle_by_thread.values():
            if lifecycle.get("status") != "interrupted":
                continue
            # The node it stopped on is LangGraph's pending ``next``; only the
            # runtime can read it, and a failed read just omits the overlay.
            state = await get_thread_state(lifecycle["thread_id"])
            if isinstance(state, dict) and isinstance(state.get("next"), list):
                lifecycle["next"] = state["next"]
            for client_id, interest in watchers.items():
                events_stream.broadcast_ui_event(
                    "agent.routine.run.interrupted",
                    lifecycle,
                    client_id=client_id,
                    interest=interest,
                )
        for entry in entries:
            lifecycle = lifecycle_by_thread.get(entry["thread_id"])
            if lifecycle is not None:
                entry["status"] = lifecycle["status"]
                if "interrupted_at" in lifecycle:
                    entry["interrupted_at"] = lifecycle["interrupted_at"]
        states: dict[str, dict[str, Any]] = {}
        for client_id, interest in watchers.items():
            if self.interests.get(client_id) is not interest:
                continue
            events_stream.broadcast_ui_event(
                "agent.routine.runs.loaded",
                {
                    "agent_id": agent_id,
                    "before": None,
                    "has_more": has_more,
                    "runs": entries,
                },
                client_id=client_id,
                interest=interest,
            )
            thread_id = interest.get("thread_id")
            if not thread_id and interest.get("session_id"):
                entry = next((r for r in entries if r["run_id"] == interest["session_id"]), None)
                thread_id = entry["thread_id"] if entry else None
            if not thread_id and not interest.get("session_id") and entries:
                thread_id = entries[0]["thread_id"]
            if thread_id:
                if thread_id not in states:
                    states[thread_id] = await read_run_state(agent_id, thread_id)
                if self.interests.get(client_id) is interest:
                    events_stream.broadcast_ui_event(
                        "agent.routine.run_state.loaded",
                        states[thread_id],
                        client_id=client_id,
                        interest=interest,
                    )

    async def lifecycle_snapshot(self, client_id: str | None = None) -> None:

        store = get_document_store()
        documents = await asyncio.to_thread(
            store.list_documents, PROGENA_STATE_PREFIX, RUN_EVENT_PREFIX
        )
        latest: dict[str, dict[str, Any]] = {}
        started: dict[str, dict[str, Any]] = {}
        for _, document in documents:
            payload = document.payload
            if not isinstance(payload, dict):
                continue
            key = payload.get("request_id")
            if not isinstance(key, str) or not key:
                continue
            if payload.get("status") == "started":
                started[key] = payload
            if payload.get("timestamp", "") >= latest.get(key, {}).get("timestamp", ""):
                latest[key] = payload
        live = live_runtime(store)
        recent = sorted(
            (
                _lifecycle_snapshot(payload, started.get(key), live=live)
                for key, payload in latest.items()
            ),
            key=lambda payload: payload.get("timestamp", ""),
            reverse=True,
        )
        active = [
            payload
            for payload in recent[100:]
            if payload.get("status") in _RETAINED_LIFECYCLE_STATUSES | {"interrupted"}
        ]
        if recent:
            events_stream.broadcast_ui_event(
                "agent.routine.lifecycle.snapshot",
                {"runs": recent[:100] + active},
                client_id=client_id,
            )

    async def reconcile(self) -> None:
        await self.lifecycle_snapshot()
        for agent_id in {i["agent_id"] for i in self.interests.values()}:
            self.schedule(agent_id)


routine_events = RoutineEvents()
