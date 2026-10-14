"""Backend clock that fires workflow ``cron`` trigger nodes.

Each cron node has one small state document (``workflow_cron:<workflow>:<node>``
in the backend namespace) holding the last due time it handled and whether the
workflow was active, so restarts resume where they left off. A node seen for
the first time, whose expression or timezone changed, or whose workflow was
just resumed or re-enabled, starts counting from now. When the backend was down
across one or more due times, the node fires once for the latest missed one.
Due times while a workflow is paused or disabled never fire.

The clock sleeps until the earliest due time; saving, pausing or deleting a
workflow wakes it to replan. Sleeps are capped so a suspended host (whose
monotonic clock stops) is noticed within a minute of waking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from app.backend.document_store import BACKEND_STATE_PREFIX, BackendDocumentStore
from app.progenitor.cron import latest_cron_time, next_cron_time
from app.progenitor.schedule.engine import parse_schedule_datetime
from app.progenitor.utils import utcnow
from app.progenitor.workflows import (
    WorkflowDefinition,
    WorkflowNodeCron,
    WorkflowRun,
    WorkflowRunSource,
    build_cron_trigger_output,
)

logger = logging.getLogger(__name__)

WORKFLOW_CRON_STATE_KEY_PREFIX = "workflow_cron:"
MAX_SLEEP_SECONDS = 60.0


class CronWorkflowTrigger(Protocol):
    async def trigger_workflow(
        self,
        workflow_id: str,
        trigger: dict[str, Any],
        source: WorkflowRunSource = ...,
        ancestry: list[str] | None = ...,
        trigger_node_id: str | None = ...,
    ) -> WorkflowRun | None: ...


def workflow_cron_state_key(workflow_id: str, node_id: str) -> str:
    return f"{WORKFLOW_CRON_STATE_KEY_PREFIX}{workflow_id}:{node_id}"


class WorkflowCronClock:
    """Single background task firing every due cron node across all workflows."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        definitions_getter: Callable[[BackendDocumentStore], list[WorkflowDefinition]],
        executor_getter: Callable[[], CronWorkflowTrigger],
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store_getter = store_getter
        self._definitions_getter = definitions_getter
        self._executor_getter = executor_getter
        self._now = now
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def wake(self) -> None:
        """Replan now (a workflow was saved, paused, resumed or deleted)."""
        self._wakeup.set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # The clock outlives app lifespans; bind the wake signal to this loop so
        # a restart under a new loop does not await an Event from the old one.
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="workflow-cron-clock")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            self._wakeup.clear()
            try:
                next_due = await self.tick()
            except Exception:
                logger.exception("Workflow cron tick failed")
                next_due = None
            delay = MAX_SLEEP_SECONDS
            if next_due is not None:
                delay = min(delay, max(0.0, (next_due - self._now()).total_seconds()))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=delay)

    async def tick(self) -> datetime | None:
        """Fire every due cron node once; return the earliest upcoming due time."""

        now = self._now()
        store = self._store_getter()
        states = {
            key: document.payload
            for key, document in store.list_documents(
                BACKEND_STATE_PREFIX, WORKFLOW_CRON_STATE_KEY_PREFIX
            )
        }
        seen: set[str] = set()
        next_wake: datetime | None = None
        for definition in self._definitions_getter(store):
            active = definition.enabled and not definition.paused
            for node_id, node in definition.graph.nodes.items():
                if not isinstance(node, WorkflowNodeCron):
                    continue
                key = workflow_cron_state_key(definition.id, node_id)
                seen.add(key)
                try:
                    due = await self._advance(
                        store, key, states.get(key), definition, node_id, node, now, active
                    )
                except Exception:
                    logger.exception(
                        "Cron node %r of workflow %r failed to fire", node_id, definition.id
                    )
                    continue
                if due is not None and (next_wake is None or due < next_wake):
                    next_wake = due
        for stale_key in states.keys() - seen:
            store.delete_document(BACKEND_STATE_PREFIX, stale_key)
        return next_wake

    async def _advance(
        self,
        store: BackendDocumentStore,
        key: str,
        state: Any,
        definition: WorkflowDefinition,
        node_id: str,
        node: WorkflowNodeCron,
        now: datetime,
        active: bool,
    ) -> datetime | None:
        def save(last_due: datetime) -> None:
            store.save_document(
                BACKEND_STATE_PREFIX,
                key,
                {
                    "expression": node.expression,
                    "timezone": node.timezone,
                    "active": active,
                    "last_due": last_due.isoformat(),
                },
            )

        anchor = None
        # A new schedule, or a workflow coming back from paused/disabled,
        # counts from now: due times inside the inactive period never fire.
        if (
            isinstance(state, dict)
            and state.get("expression") == node.expression
            and state.get("timezone") == node.timezone
            and (state.get("active") is True or not active)
        ):
            anchor = parse_schedule_datetime(state.get("last_due"))
        if anchor is None:
            save(now)
            anchor = now
        elif state.get("active") is not active:
            save(anchor)

        due = next_cron_time(node.expression, anchor, node.timezone)
        if due > now:
            return due if active else None
        latest = latest_cron_time(node.expression, now, node.timezone)
        # Record before firing: a crash mid-fire loses that one fire rather
        # than running it twice.
        save(latest)
        if active:
            await self._executor_getter().trigger_workflow(
                definition.id,
                build_cron_trigger_output(node, scheduled_for=latest, fired_at=now),
                source="cron",
                trigger_node_id=node_id,
            )
        return next_cron_time(node.expression, latest, node.timezone) if active else None
