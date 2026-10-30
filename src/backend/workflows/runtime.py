"""Workflows runtime."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping
from typing import Any, Literal

import src.backend.chat.threads as chat_threads
import src.backend.events.contracts as events_contracts
import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.lifecycle.state as lifecycle_state
import src.backend.storage.documents as storage_documents
import src.backend.workflows.acp_supervisor as workflows_acp_supervisor
import src.backend.workflows.cron as workflows_cron
import src.backend.workflows.events as workflows_events
import src.backend.workflows.models as workflows_models
import src.backend.workflows.service as workflows_service
from src.progena.documents.kinds import (
    WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
)
from src.progena.routines.events import RUN_EVENT_PREFIX
from src.progena.workflows.documents import parse_workflow_invoke_request_document
from src.progena.workflows.models import WorkflowDefinition, WorkflowListItem, WorkflowRun

logger = logging.getLogger(__name__)


def _resolve_action_thread_id(thread_id: str | None) -> str:
    """Return a workspace execution thread identifier."""

    normalized = str(thread_id or "").strip()
    if normalized:
        return normalized
    return f"thread_{uuid.uuid4().hex[:12]}"


def _build_action_metadata(
    *,
    thread_id: str,
    invocation_type: Literal["user", "automation", "system", "workflow_step"],
    invocation_source: str | None,
    widget_id: str | None,
    root_widget_id: str | None,
    action: str,
    workflow_run_id: str | None = None,
) -> dict[str, str]:
    """Build metadata sent through the backend→Progena boundary."""

    metadata = {
        "thread_id": thread_id,
        "invocation_type": invocation_type,
        "invocation_source": invocation_source or action,
        "action": action,
        "source": http_contracts.SOURCE_WORKFLOW
        if invocation_type == "workflow_step"
        else http_contracts.SOURCE_UI,
    }
    if widget_id:
        metadata["widget_id"] = widget_id
    if root_widget_id:
        metadata["root_widget_id"] = root_widget_id
    if workflow_run_id:
        metadata["workflow_run_id"] = workflow_run_id
    return metadata


_workflow_executor: workflows_service.WorkflowExecutor | None = None


def _workflow_event_request_id(payload: Mapping[str, Any]) -> str | None:
    """Recover a finite UI request correlation stored on a workflow trigger."""
    trigger = payload.get("trigger_event")
    request_id = trigger.get("request_id") if isinstance(trigger, Mapping) else None
    return request_id if isinstance(request_id, str) else None


async def _workflow_prompt_runner(
    *,
    workflow: WorkflowDefinition,
    run_id: str,
    node_id: str,
    node,
    input_text: str,
    thread_id: str | None,
) -> workflows_service.PromptExecutionResult:
    """Execute every agent/extract workflow step through one normal prompt path.

    ``input_text`` is the rendered prompt (plus any appended instructions);
    it is the sole instruction sent to the model.
    """

    widget_id = f"workflow:{workflow.id}:{node_id}"
    request = workflows_models.ActionRunRequest(
        widget_id=widget_id,
        input=input_text,
        broadcast_response=False,
        thread_id=thread_id,
        invocation_type="workflow_step",
        invocation_source=widget_id,
        root_widget_id=f"workflow:{workflow.id}",
    )
    thread_id, response_obj, response_text = await _execute_action_run(
        request, workflow_run_id=run_id
    )
    return workflows_service.PromptExecutionResult(
        response=response_text,
        thread_id=thread_id,
        usage=getattr(response_obj, "usage", None),
    )


def get_workflow_executor() -> workflows_service.WorkflowExecutor:
    """Return the singleton backend workflow executor."""
    global _workflow_executor
    if _workflow_executor is None:
        _workflow_executor = workflows_service.WorkflowExecutor(
            store_getter=storage_documents.get_default_document_store,
            prompt_runner=_workflow_prompt_runner,
            emit_event=lambda event_type, payload: events_stream.broadcast_ui_event(
                event_type,
                payload,
                request_id=_workflow_event_request_id(payload),
            ),
        )
    return _workflow_executor


_workflow_cron_clock: workflows_cron.WorkflowCronClock | None = None


def get_workflow_cron_clock() -> workflows_cron.WorkflowCronClock:
    """Return the singleton clock firing workflow cron trigger nodes."""
    global _workflow_cron_clock
    if _workflow_cron_clock is None:
        _workflow_cron_clock = workflows_cron.WorkflowCronClock(
            store_getter=storage_documents.get_default_document_store,
            definitions_getter=workflows_service.list_workflow_definitions,
            executor_getter=get_workflow_executor,
        )
    return _workflow_cron_clock


_workflow_invoke_lock = asyncio.Lock()


async def _reconcile_workflow_invoke_requests(changed_key: str | None = None) -> None:
    """Consume durable workflow invocation requests from Progena."""

    if changed_key is not None and not changed_key.startswith(WORKFLOW_INVOKE_REQUEST_KEY_PREFIX):
        return
    store = storage_documents.get_default_document_store()
    rows = store.list_documents(
        storage_documents.PROGENA_STATE_PREFIX, WORKFLOW_INVOKE_REQUEST_KEY_PREFIX
    )
    if not rows:
        return
    executor = get_workflow_executor()
    async with _workflow_invoke_lock:
        for key, document in rows:
            payload = document.payload if isinstance(document.payload, dict) else {}
            try:
                request = parse_workflow_invoke_request_document(payload)
            except Exception:
                logger.warning("Dropping malformed workflow invoke request %r", key, exc_info=True)
                store.delete_document(storage_documents.PROGENA_STATE_PREFIX, key)
                continue
            try:
                definition = workflows_service.get_workflow_definition(store, request.workflow_id)
                if definition is not None:
                    try:
                        trigger = workflows_service.build_manual_trigger_event(
                            definition, payload=request.input
                        )
                    except ValueError as exc:
                        logger.warning(
                            "Dropping workflow invoke request %r: %s",
                            request.request_id,
                            exc,
                        )
                        store.delete_document(storage_documents.PROGENA_STATE_PREFIX, key)
                        continue
                    await executor.trigger_workflow(
                        request.workflow_id,
                        trigger,
                        source="progena",
                        ancestry=request.ancestry,
                        run_id=request.run_id,
                    )
            except Exception:
                logger.exception(
                    "Workflow invoke request %r failed; retaining it for retry",
                    request.request_id,
                )
                continue
            store.delete_document(storage_documents.PROGENA_STATE_PREFIX, key)


async def _apply_workflow_approval_request(typed: events_documents.TypedDocumentEvent) -> None:
    """Apply Progena's answer to a workflow approval, then drop the request.

    Progena answers as the agent, so a human-only approval is refused (and
    logged) rather than applied. The request is single-use either way.
    """

    event = typed.event
    if event.op != "created" or not event.key.startswith(WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX):
        return
    request = event.after if isinstance(event.after, dict) else {}
    workflow_id, run_id = request.get("workflow_id"), request.get("run_id")
    approved = request.get("approved")
    store = storage_documents.get_default_document_store()
    if isinstance(workflow_id, str) and isinstance(run_id, str) and isinstance(approved, bool):
        try:
            resolved = await get_workflow_executor().resolve_approval(
                workflow_id, run_id, approved=approved, answered_by="agent"
            )
        except PermissionError as exc:
            logger.warning("Refused Progena approval for %s/%s: %s", workflow_id, run_id, exc)
        else:
            if resolved is None:
                logger.info("Approval request for %s/%s no longer applies", workflow_id, run_id)
    else:
        logger.warning("Dropping malformed workflow approval request %r", event.key)
    store.delete_document(storage_documents.PROGENA_STATE_PREFIX, event.key)


def _workflow_content_response(
    definition: WorkflowDefinition,
) -> workflows_models.WorkflowContentResponse:
    return workflows_models.WorkflowContentResponse(
        path=f"/workflows/{definition.id}.json",
        workflow=definition,
    )


def _workflow_event_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return _workflow_content_response(definition).model_dump(mode="json", by_alias=True)


async def _execute_action_run(
    body: workflows_models.ActionRunRequest, *, workflow_run_id: str | None = None
) -> tuple[str, Any, str]:
    """Run an action internally and return its thread ID, response, and text.

    Internal callers do not receive an HTTP acknowledgement envelope.
    """

    if not lifecycle_state.client:
        raise RuntimeError("Client not initialized")

    code = body.code or body.content
    segments = []
    if body.widget_id:
        segments.append(f"Widget ID: {body.widget_id}")
    if body.language and code:
        segments.append(f"Run this {body.language} code:\n{code}")
    elif code:
        segments.append(f"Run this code:\n{code}")
    if body.prompt:
        segments.append(f"Prompt:\n{body.prompt}")
    if body.input:
        segments.append(f"Input:\n{body.input}")

    if not segments:
        raise ValueError("No run payload provided")

    thread_id = _resolve_action_thread_id(body.thread_id)
    metadata = _build_action_metadata(
        thread_id=thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source,
        widget_id=body.widget_id,
        root_widget_id=body.root_widget_id,
        action=body.invocation_source or body.language or "workspace.run",
        workflow_run_id=workflow_run_id,
    )
    chat_threads.upsert_thread(
        thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source or "workspace.run",
        root_widget_id=body.root_widget_id or body.widget_id,
        widget_id=body.widget_id,
    )

    try:
        response = await lifecycle_state.client.send_message(
            "\n\n".join(segments), metadata=metadata
        )
    except Exception as exc:
        chat_threads.fail_thread(thread_id, str(exc), widget_id=body.widget_id)
        raise
    chat_threads.complete_thread(thread_id, response.response, widget_id=body.widget_id)
    return (thread_id, response, response.response)


_active_workflow_invoke_tasks: set[asyncio.Task] = set()


def _queue_workflow_invoke_reconciliation(_snapshot, changed_key: str | None = None) -> None:
    """Queue reconciliation for durable Progena workflow requests."""

    task = asyncio.create_task(_reconcile_workflow_invoke_requests(changed_key))
    _active_workflow_invoke_tasks.add(task)
    task.add_done_callback(_active_workflow_invoke_tasks.discard)


_acp_task_supervisor: workflows_acp_supervisor.ACPTaskSupervisor | None = None


def get_acp_task_supervisor() -> workflows_acp_supervisor.ACPTaskSupervisor:
    """Return the backend process's singleton ACP task supervisor."""
    global _acp_task_supervisor
    if _acp_task_supervisor is None:
        _acp_task_supervisor = workflows_acp_supervisor.ACPTaskSupervisor(
            store_getter=storage_documents.get_default_document_store
        )
    return _acp_task_supervisor


async def _publish_routine_run_lifecycle(typed: events_documents.TypedDocumentEvent) -> None:
    """Broadcast routine run lifecycle transitions logged as ``routine_run_event:`` rows."""

    await workflows_events.routine_events.document_event(typed.event)


async def _advance_workflows_on_routine_event(typed: events_documents.TypedDocumentEvent) -> None:
    """Hand routine lifecycle transitions to workflow runs waiting on a routine node."""

    event = typed.event
    if event.op == "deleted" or not event.key.startswith(RUN_EVENT_PREFIX):
        return
    if isinstance(event.after, dict):
        await get_workflow_executor().handle_routine_run_event(event.after)


async def _dispatch_document_event_workflows(typed: events_documents.TypedDocumentEvent) -> None:
    """Start workflows subscribed to this event's document type."""

    executor = get_workflow_executor()
    if typed.event.key.startswith(WORKFLOW_DEFINITION_KEY_PREFIX):
        executor.invalidate_subscriptions()
        get_workflow_cron_clock().workflow_changed(typed.event.after)
    if typed.output is None:
        return
    await executor.dispatch_document_event(typed.output, origin=typed.event.origin)


def broadcast_workflows_snapshot(
    items: list[WorkflowListItem] | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current workflow definition list."""

    if items is None:
        items = workflows_service.list_workflow_list_items(
            storage_documents.get_default_document_store()
        )
    payload = [item.model_dump(mode="json") for item in items]
    return events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOWS_SNAPSHOT, {"items": payload}, request_id=request_id
    )


def broadcast_workflow_detail(workflow_id: str) -> events_contracts.UiEventEnvelope:
    """Broadcast one workflow definition update or delete event."""

    definition = workflows_service.get_workflow_definition(
        storage_documents.get_default_document_store(), workflow_id
    )
    if definition is None:
        return events_stream.broadcast_ui_event(
            events_contracts.UiEvent.WORKFLOW_DELETED, {"workflow_id": workflow_id}
        )
    payload = _workflow_content_response(definition).model_dump(mode="json", by_alias=True)
    return events_stream.broadcast_ui_event(events_contracts.UiEvent.WORKFLOW_UPDATED, payload)


def broadcast_workflow_runs_snapshot(
    runs: list[WorkflowRun] | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the current workflow runs list."""

    if runs is None:
        runs = workflows_service.list_workflow_runs(storage_documents.get_default_document_store())
    payload = [item.model_dump(mode="json") for item in runs]
    return events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_RUNS_SNAPSHOT, {"items": payload}, request_id=request_id
    )
