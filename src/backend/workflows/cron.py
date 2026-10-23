"""Backend clock that fires workflow ``cron`` trigger nodes.

Each cron node has one small state document (``workflow_cron:<workflow>:<node>``
in the backend namespace, each id percent-encoded) holding the last due time it
handled and whether the workflow was active, so restarts resume where they left
off. A node seen for the first time, whose expression or timezone changed, or
whose workflow was just resumed or re-enabled, starts counting from now. When
the backend was down across one or more due times, the node fires once for the
latest missed one. Due times while a workflow is paused or disabled never fire:
every pause or disable is recorded from the workflow's document event, so a
resume before the next tick still counts from the resume.

Each due time is claimed with an atomic read-modify-write of the state
document before firing, so backends sharing one database fire it once.

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
from urllib.parse import quote

from pydantic import ValidationError

from src.backend.storage.documents import BACKEND_STATE_PREFIX, BackendDocumentStore
from src.progena.cron import latest_cron_time, next_cron_time
from src.progena.schedules.engine import parse_schedule_datetime
from src.progena.utils import utcnow
from src.progena.workflows.constants import WorkflowRunSource
from src.progena.workflows.events import build_cron_trigger_output
from src.progena.workflows.models import WorkflowDefinition, WorkflowNodeCron, WorkflowRun

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
    return (
        f"{WORKFLOW_CRON_STATE_KEY_PREFIX}{quote(workflow_id, safe='')}:{quote(node_id, safe='')}"
    )


def _next_state(
    state: Any, node: WorkflowNodeCron, now: datetime, active: bool
) -> tuple[dict[str, Any], datetime | None]:
    """The node's state after a tick at ``now``, and the due time to fire (if any).

    A new schedule, or a workflow coming back from paused/disabled, counts
    from now: due times inside the inactive period never fire.
    """
    anchor = None
    if (
        isinstance(state, dict)
        and state.get("expression") == node.expression
        and state.get("timezone") == node.timezone
        and (state.get("active") is True or not active)
    ):
        anchor = parse_schedule_datetime(state.get("last_due"))
    last_due = anchor or now
    fire_at = None
    if anchor is not None and next_cron_time(node.expression, anchor, node.timezone) <= now:
        last_due = latest_cron_time(node.expression, now, node.timezone)
        fire_at = last_due if active else None
    new_state = {
        "expression": node.expression,
        "timezone": node.timezone,
        "active": active,
        "last_due": last_due.isoformat(),
    }
    return new_state, fire_at


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

    def workflow_changed(self, payload: Any) -> None:
        """Record a workflow definition write, then replan.

        Called for every ``workflow:`` document event in log order. A paused or
        disabled workflow marks its cron nodes inactive here, independent of
        whether a tick saw it, so a resume always counts from then on.
        """
        try:
            definition = WorkflowDefinition.model_validate(payload) if payload else None
        except ValidationError:
            logger.warning("Cron clock ignored an invalid workflow document", exc_info=True)
            definition = None
        if definition is not None and not (definition.enabled and not definition.paused):
            store = self._store_getter()
            for node_id, node in definition.graph.nodes.items():
                if isinstance(node, WorkflowNodeCron):
                    store.merge_document(
                        BACKEND_STATE_PREFIX,
                        workflow_cron_state_key(definition.id, node_id),
                        {"active": False},
                    )
        self.wake()

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
        new_state, fire_at = _next_state(state, node, now, active)
        if new_state != state:
            # Claim atomically against the stored state, which another backend
            # may have advanced since ``state`` was read: only the claimant fires.
            # Recorded before firing: a crash mid-fire loses that one fire
            # rather than running it twice.
            claimed: list[datetime | None] = [None]

            def claim(current: Any) -> dict[str, Any]:
                claimed_state, claimed[0] = _next_state(current, node, now, active)
                return claimed_state

            new_state = store.mutate_document(BACKEND_STATE_PREFIX, key, claim)
            fire_at = claimed[0]
        if fire_at is not None:
            await self._executor_getter().trigger_workflow(
                definition.id,
                build_cron_trigger_output(node, scheduled_for=fire_at, fired_at=now),
                source="cron",
                trigger_node_id=node_id,
            )
        if not active:
            return None
        last_due = parse_schedule_datetime(new_state["last_due"])
        assert last_due is not None
        return next_cron_time(node.expression, last_due, node.timezone)
