"""Workflow definition storage and backend execution helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.backend.document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from app.progenitor.utils import utcnow_isoformat
from app.progenitor.workflows import (
    WORKFLOW_ALLOWED_EVENT_TYPES,
    WorkflowDefinition,
    WorkflowListItem,
    WorkflowNodeEvent,
    WorkflowNodePrompt,
    WorkflowRun,
    build_workflow_list_item,
    coerce_workflow_definition,
    coerce_workflow_run,
    ordered_workflow_node_ids,
    validate_workflow_definition_payload,
    workflow_definition_key,
    workflow_id_from_path,
    workflow_queue_key,
    workflow_run_key,
)

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "paused"})


@dataclass(frozen=True)
class PromptExecutionResult:
    """Minimal result surface the executor needs from a prompt invocation."""

    response: str
    thread_id: str | None = None


class WorkflowPromptRunner(Protocol):
    """Prompt runner adapter for workflow prompt nodes."""

    async def __call__(
        self,
        *,
        workflow: WorkflowDefinition,
        node_id: str,
        node: WorkflowNodePrompt,
        input_text: str,
        thread_id: str | None,
    ) -> PromptExecutionResult: ...


def list_workflow_definitions(store: BackendDocumentStore) -> list[WorkflowDefinition]:
    """Return every valid stored workflow definition, newest first."""

    rows = store.list_documents(PROGENITOR_STATE_PREFIX, "workflow:")
    definitions: list[WorkflowDefinition] = []
    for _key, document in rows:
        try:
            definitions.append(coerce_workflow_definition(_document_payload(document, {})))
        except Exception:
            logger.warning("Skipping invalid workflow definition document", exc_info=True)
    definitions.sort(
        key=lambda item: (item.updated_at, item.created_at, item.id),
        reverse=True,
    )
    return definitions


def list_workflow_list_items(store: BackendDocumentStore) -> list[WorkflowListItem]:
    """Return workflow sidebar/list payloads."""

    return [build_workflow_list_item(item) for item in list_workflow_definitions(store)]


def get_workflow_definition(
    store: BackendDocumentStore,
    workflow_id_or_path: str,
) -> WorkflowDefinition | None:
    """Return one stored workflow definition."""

    workflow_id = workflow_id_from_path(workflow_id_or_path)
    document = store.get_document(
        PROGENITOR_STATE_PREFIX, workflow_definition_key(workflow_id), None
    )
    if document is None:
        return None
    return coerce_workflow_definition(_document_payload(document, {}))


def save_workflow_definition(
    store: BackendDocumentStore,
    payload: Mapping[str, Any],
) -> WorkflowDefinition:
    """Validate and persist one workflow definition."""

    normalized = validate_workflow_definition_payload(dict(payload))
    definition = WorkflowDefinition.model_validate(normalized)
    store.save_document(
        PROGENITOR_STATE_PREFIX,
        workflow_definition_key(definition.id),
        definition.model_dump(mode="json", by_alias=True),
    )
    return definition


def delete_workflow_definition(store: BackendDocumentStore, workflow_id_or_path: str) -> None:
    """Delete one workflow definition."""

    workflow_id = workflow_id_from_path(workflow_id_or_path)
    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_definition_key(workflow_id))


def list_workflow_runs(
    store: BackendDocumentStore,
    *,
    workflow_id: str | None = None,
) -> list[WorkflowRun]:
    """Return persisted workflow runs, newest first."""

    rows = store.list_documents(PROGENITOR_STATE_PREFIX, "workflow_run:")
    runs: list[WorkflowRun] = []
    for _key, document in rows:
        try:
            run = coerce_workflow_run(_document_payload(document, {}))
        except Exception:
            logger.warning("Skipping invalid workflow run document", exc_info=True)
            continue
        if workflow_id is not None and run.workflow_id != workflow_id:
            continue
        runs.append(run)
    runs.sort(
        key=lambda item: (item.updated_at, item.queued_at, item.run_id),
        reverse=True,
    )
    return runs


def get_workflow_run(store: BackendDocumentStore, run_id: str) -> WorkflowRun | None:
    """Return one stored workflow run."""

    document = store.get_document(PROGENITOR_STATE_PREFIX, workflow_run_key(run_id), None)
    if document is None:
        return None
    return coerce_workflow_run(_document_payload(document, {}))


def save_workflow_run(store: BackendDocumentStore, run: WorkflowRun) -> WorkflowRun:
    """Persist one workflow run."""

    store.save_document(
        PROGENITOR_STATE_PREFIX,
        workflow_run_key(run.run_id),
        run.model_dump(mode="json"),
    )
    return run


def delete_workflow_run(store: BackendDocumentStore, run_id: str) -> None:
    """Delete one workflow run."""

    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_run_key(run_id))


def get_workflow_queue(store: BackendDocumentStore, workflow_id: str) -> list[dict[str, Any]]:
    """Return queued trigger payloads for one workflow."""

    document = store.get_document(PROGENITOR_STATE_PREFIX, workflow_queue_key(workflow_id), None)
    if document is None:
        return []
    payload = _document_payload(document, {})
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def save_workflow_queue(
    store: BackendDocumentStore,
    workflow_id: str,
    items: list[dict[str, Any]],
) -> None:
    """Persist queued trigger payloads for one workflow."""

    if items:
        store.save_document(
            PROGENITOR_STATE_PREFIX,
            workflow_queue_key(workflow_id),
            {"items": [item for item in items if isinstance(item, dict)]},
        )
        return
    store.delete_document(PROGENITOR_STATE_PREFIX, workflow_queue_key(workflow_id))


def find_active_workflow_run(
    store: BackendDocumentStore,
    workflow_id: str,
) -> WorkflowRun | None:
    """Return the newest active run for one workflow, if any."""

    for run in list_workflow_runs(store, workflow_id=workflow_id):
        if run.status in _ACTIVE_RUN_STATUSES:
            return run
    return None


def build_manual_trigger_event(
    definition: WorkflowDefinition,
    *,
    event_type: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a trigger payload for a manual operator-triggered workflow run."""

    resolved_type = str(event_type or definition.trigger.event_type).strip().lower()
    if resolved_type not in WORKFLOW_ALLOWED_EVENT_TYPES:
        raise ValueError(f"unsupported workflow trigger event type: {resolved_type}")
    timestamp = utcnow_isoformat()
    event_payload = dict(payload or {})
    event_payload.setdefault("event_type", resolved_type)
    event_payload.setdefault("timestamp", timestamp)
    event_payload.setdefault("source", "manual")
    return {
        "event_type": resolved_type,
        "timestamp": timestamp,
        "payload": event_payload,
    }


def compose_workflow_prompt_input(trigger_event: Mapping[str, Any], previous_output: str) -> str:
    """Compose the default prompt input for a workflow prompt node."""

    event_type = str(trigger_event.get("event_type") or "").strip()
    payload = trigger_event.get("payload")
    payload_dict = payload if isinstance(payload, Mapping) else {}
    card = payload_dict.get("card") if isinstance(payload_dict.get("card"), Mapping) else None
    card_title = str(card.get("title") or "").strip() if isinstance(card, Mapping) else ""
    summary_suffix = f" for card '{card_title}'" if card_title else ""
    sections = [
        f"Event summary: {event_type or 'workflow event'}{summary_suffix}",
        "Event payload JSON:",
        json.dumps(payload_dict, indent=2, sort_keys=True, default=str),
    ]
    if previous_output.strip():
        sections.extend(
            [
                "",
                "Previous workflow node output:",
                previous_output,
            ]
        )
    return "\n".join(sections)


def render_workflow_event_output(trigger_event: Mapping[str, Any]) -> str:
    """Render the event node's output text for downstream prompt nodes."""

    payload = trigger_event.get("payload")
    payload_dict = payload if isinstance(payload, Mapping) else {}
    event_type = str(
        trigger_event.get("event_type") or payload_dict.get("event_type") or ""
    ).strip()
    return json.dumps(
        {
            "event_type": event_type,
            "payload": payload_dict,
        },
        indent=2,
        sort_keys=True,
        default=str,
    )


class WorkflowExecutor:
    """Backend-resident workflow executor for v1 event -> prompt chains."""

    def __init__(
        self,
        *,
        store_getter: Callable[[], BackendDocumentStore],
        prompt_runner: WorkflowPromptRunner,
        emit_event: Callable[[str, Mapping[str, Any]], Any],
    ) -> None:
        self._store_getter = store_getter
        self._prompt_runner = prompt_runner
        self._emit_event = emit_event
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def dispatch_triggers(self, triggers: list[dict[str, Any]]) -> list[WorkflowRun]:
        """Dispatch a batch of trigger payloads to matching workflows."""

        started: list[WorkflowRun] = []
        for trigger in triggers:
            event_type = str(trigger.get("event_type") or "").strip().lower()
            if not event_type:
                continue
            started.extend(await self.dispatch_event(event_type, trigger))
        return started

    async def dispatch_event(self, event_type: str, trigger: dict[str, Any]) -> list[WorkflowRun]:
        """Dispatch one event payload to all matching workflows."""

        store = self._store_getter()
        definitions = [
            definition
            for definition in list_workflow_definitions(store)
            if definition.trigger.event_type == event_type
        ]
        started: list[WorkflowRun] = []
        for definition in definitions:
            run = await self.trigger_workflow(definition.id, trigger)
            if run is not None:
                started.append(run)
        return started

    async def trigger_workflow(
        self,
        workflow_id: str,
        trigger: Mapping[str, Any],
    ) -> WorkflowRun | None:
        """Start or queue a run for one workflow definition."""

        async with self._lock_for(workflow_id):
            store = self._store_getter()
            definition = get_workflow_definition(store, workflow_id)
            if definition is None or not definition.enabled or definition.paused:
                return None

            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is not None:
                queue = get_workflow_queue(store, workflow_id)
                queue.append(dict(trigger))
                save_workflow_queue(store, workflow_id, queue)
                return active_run

            now = utcnow_isoformat()
            run = WorkflowRun(
                run_id=f"workflow_run_{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                status="queued",
                trigger_event=dict(trigger),
                queued_at=now,
                updated_at=now,
            )
            save_workflow_run(store, run)
            self._start_task(workflow_id, run.run_id)
            return run

    async def pause_workflow(self, workflow_id: str) -> WorkflowRun | None:
        """Pause the active run for one workflow, if present."""

        async with self._lock_for(workflow_id):
            store = self._store_getter()
            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is None:
                return None
            if active_run.status != "paused":
                active_run = active_run.model_copy(
                    update={
                        "status": "paused",
                        "updated_at": utcnow_isoformat(),
                    }
                )
                save_workflow_run(store, active_run)
                self._emit_event("workflow.run.updated", active_run.model_dump(mode="json"))
            return active_run

    async def resume_workflow(self, workflow_id: str) -> WorkflowRun | None:
        """Resume a paused run or start the next queued trigger."""

        candidate: WorkflowRun | None = None
        async with self._lock_for(workflow_id):
            store = self._store_getter()
            active_run = find_active_workflow_run(store, workflow_id)
            if active_run is not None and active_run.status == "paused":
                candidate = active_run.model_copy(
                    update={
                        "status": "queued",
                        "updated_at": utcnow_isoformat(),
                    }
                )
                save_workflow_run(store, candidate)
            elif active_run is None:
                queue = get_workflow_queue(store, workflow_id)
                if queue:
                    now = utcnow_isoformat()
                    candidate = WorkflowRun(
                        run_id=f"workflow_run_{uuid.uuid4().hex[:12]}",
                        workflow_id=workflow_id,
                        status="queued",
                        trigger_event=queue.pop(0),
                        queued_at=now,
                        updated_at=now,
                    )
                    save_workflow_run(store, candidate)
                    save_workflow_queue(store, workflow_id, queue)
        if candidate is not None:
            self._start_task(workflow_id, candidate.run_id)
        return candidate

    def _lock_for(self, workflow_id: str) -> asyncio.Lock:
        lock = self._locks.get(workflow_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[workflow_id] = lock
        return lock

    def _start_task(self, workflow_id: str, run_id: str) -> None:
        existing = self._tasks.get(workflow_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._execute_run(workflow_id, run_id))
        self._tasks[workflow_id] = task

        def _cleanup(_task: asyncio.Task[None]) -> None:
            current = self._tasks.get(workflow_id)
            if current is _task:
                self._tasks.pop(workflow_id, None)

        task.add_done_callback(_cleanup)

    async def _execute_run(self, workflow_id: str, run_id: str) -> None:
        try:
            await self._execute_run_locked(workflow_id, run_id)
        except Exception:
            logger.exception("Workflow execution failed for %s", workflow_id)

    async def _execute_run_locked(self, workflow_id: str, run_id: str) -> None:
        store = self._store_getter()
        run = get_workflow_run(store, run_id)
        if run is None:
            return
        definition = get_workflow_definition(store, workflow_id)
        if definition is None:
            self._mark_failed(
                store,
                run,
                error="Workflow definition no longer exists",
            )
            await self._maybe_start_next_run_locked(store, workflow_id)
            return
        if not definition.enabled:
            self._mark_failed(store, run, error="Workflow is disabled")
            await self._maybe_start_next_run_locked(store, workflow_id)
            return
        if definition.paused or run.status == "paused":
            paused_run = run.model_copy(
                update={"status": "paused", "updated_at": utcnow_isoformat()}
            )
            save_workflow_run(store, paused_run)
            self._emit_event("workflow.run.updated", paused_run.model_dump(mode="json"))
            return

        if run.status == "queued":
            run = run.model_copy(
                update={
                    "status": "running",
                    "started_at": run.started_at or utcnow_isoformat(),
                    "updated_at": utcnow_isoformat(),
                }
            )
            save_workflow_run(store, run)
            self._emit_event("workflow.run.started", run.model_dump(mode="json"))

        node_order = ordered_workflow_node_ids(definition)
        completed = list(run.completed_node_ids)
        next_index = len(completed)
        if next_index > len(node_order):
            self._mark_failed(store, run, error="Run checkpoint references more nodes than exist")
            await self._maybe_start_next_run_locked(store, workflow_id)
            return

        last_output = str(run.last_output or "")
        thread_id = run.thread_id
        for node_id in node_order[next_index:]:
            definition = get_workflow_definition(store, workflow_id) or definition
            latest_run = get_workflow_run(store, run_id) or run
            if not definition.enabled:
                self._mark_failed(store, latest_run, error="Workflow was disabled during execution")
                await self._maybe_start_next_run_locked(store, workflow_id)
                return
            if definition.paused or latest_run.status == "paused":
                paused_run = latest_run.model_copy(
                    update={"status": "paused", "updated_at": utcnow_isoformat()}
                )
                save_workflow_run(store, paused_run)
                self._emit_event("workflow.run.updated", paused_run.model_dump(mode="json"))
                return

            node = definition.graph.nodes[node_id]
            try:
                if isinstance(node, WorkflowNodeEvent):
                    output_text = render_workflow_event_output(latest_run.trigger_event)
                else:
                    input_text = compose_workflow_prompt_input(
                        latest_run.trigger_event, last_output
                    )
                    prompt_result = await self._prompt_runner(
                        workflow=definition,
                        node_id=node_id,
                        node=node,
                        input_text=input_text,
                        thread_id=thread_id,
                    )
                    output_text = prompt_result.response
                    thread_id = prompt_result.thread_id or thread_id
            except Exception as exc:
                self._mark_failed(store, latest_run, error=str(exc))
                await self._maybe_start_next_run_locked(store, workflow_id)
                return

            definition_after = get_workflow_definition(store, workflow_id) or definition
            latest_run_after = get_workflow_run(store, run_id) or latest_run
            completed = list(latest_run_after.completed_node_ids)
            if node_id not in completed:
                completed.append(node_id)
            paused_after_node = definition_after.paused or latest_run_after.status == "paused"
            run = latest_run_after.model_copy(
                update={
                    "status": "paused" if paused_after_node else "running",
                    "current_node_id": node_id,
                    "completed_node_ids": completed,
                    "thread_id": thread_id,
                    "last_output": output_text,
                    "updated_at": utcnow_isoformat(),
                }
            )
            last_output = output_text
            save_workflow_run(store, run)
            self._emit_event("workflow.run.updated", run.model_dump(mode="json"))
            if paused_after_node:
                return
            if not definition_after.enabled:
                self._mark_failed(store, run, error="Workflow was disabled during execution")
                await self._maybe_start_next_run_locked(store, workflow_id)
                return

        run = run.model_copy(
            update={
                "status": "completed",
                "updated_at": utcnow_isoformat(),
                "completed_at": utcnow_isoformat(),
            }
        )
        save_workflow_run(store, run)
        self._emit_event("workflow.run.completed", run.model_dump(mode="json"))
        await self._maybe_start_next_run_locked(store, workflow_id)

    async def _maybe_start_next_run_locked(
        self,
        store: BackendDocumentStore,
        workflow_id: str,
    ) -> None:
        async with self._lock_for(workflow_id):
            definition = get_workflow_definition(store, workflow_id)
            if definition is None or not definition.enabled or definition.paused:
                return
            queue = get_workflow_queue(store, workflow_id)
            if not queue:
                save_workflow_queue(store, workflow_id, [])
                return
            now = utcnow_isoformat()
            next_run = WorkflowRun(
                run_id=f"workflow_run_{uuid.uuid4().hex[:12]}",
                workflow_id=workflow_id,
                status="queued",
                trigger_event=queue.pop(0),
                queued_at=now,
                updated_at=now,
            )
            save_workflow_run(store, next_run)
            save_workflow_queue(store, workflow_id, queue)
            # Hand the slot over to the queued run now; otherwise `_start_task()`
            # can see the finishing task still registered and refuse to launch the
            # successor even though this current run is done.
            self._tasks.pop(workflow_id, None)
            asyncio.get_running_loop().call_soon(self._start_task, workflow_id, next_run.run_id)

    def _mark_failed(
        self,
        store: BackendDocumentStore,
        run: WorkflowRun,
        *,
        error: str,
    ) -> WorkflowRun:
        failed = run.model_copy(
            update={
                "status": "failed",
                "last_error": error,
                "updated_at": utcnow_isoformat(),
                "completed_at": utcnow_isoformat(),
            }
        )
        save_workflow_run(store, failed)
        self._emit_event("workflow.run.failed", failed.model_dump(mode="json"))
        return failed


def _document_payload(document: Any, default: Any) -> Any:
    if document is None:
        return default
    payload = getattr(document, "payload", document)
    return payload if payload is not None else default
