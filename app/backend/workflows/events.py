"""Routine notifications: global lifecycle, connection-scoped checkpoint detail."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any

import app.backend.events.stream as events_stream
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    DocumentEvent,
    StoreChangeListener,
    store_database_url,
)
from app.backend.storage.documents import get_default_document_store as get_document_store
from app.progenitor.routines.approvals import decision_key, pending_approvals
from app.progenitor.routines.events import CHECKPOINT_CHANNEL, RUN_EVENT_PREFIX

logger = logging.getLogger(__name__)


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
                        PROGENITOR_STATE_PREFIX,
                        decision_key(thread_id, approval["interrupt_id"]),
                    )
                    approval["answer_pending"] = claim is not None
                payload["pending_approvals"] = approvals
    return payload


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
        runs, has_more = await asyncio.to_thread(get_document_store().list_routine_runs, agent_id)
        entries = [asdict(run) for run in runs]
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

        documents = await asyncio.to_thread(
            get_document_store().list_documents, PROGENITOR_STATE_PREFIX, RUN_EVENT_PREFIX
        )
        latest: dict[str, dict[str, Any]] = {}
        for _, document in documents:
            payload = document.payload
            if not isinstance(payload, dict):
                continue
            key = payload.get("request_id")
            if key and payload.get("timestamp", "") >= latest.get(key, {}).get("timestamp", ""):
                latest[key] = payload
        # Preserve active attempts even when there are many recent terminal runs.
        recent = sorted(latest.values(), key=lambda p: p.get("timestamp", ""), reverse=True)
        active = [p for p in recent[100:] if p.get("status") in {"enqueued", "started", "paused"}]
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
