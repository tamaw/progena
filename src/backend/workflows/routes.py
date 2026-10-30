"""Workflows routes."""

from __future__ import annotations

import logging
import time
from datetime import UTC
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request

import src.backend.chat.threads as chat_threads
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.lifecycle.state as lifecycle_state
import src.backend.monitoring.state as monitoring_state
import src.backend.schedules.service as schedules_service
import src.backend.settings.preferences_store as preferences_store
import src.backend.storage.documents as storage_documents
import src.backend.workflows.acp_results as acp_results
import src.backend.workflows.models as workflows_models
import src.backend.workflows.runtime as workflows_runtime
import src.backend.workflows.service as workflows_service
from src.backend.events import contracts as events_contracts
from src.progena.cron import cron_error, next_cron_time, timezone_name
from src.progena.utils import utcnow
from src.progena.workflows.documents import default_workflow_definition, workflow_id_from_path

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/acp/tasks/{task_id}/result")
async def get_acp_task_result(
    task_id: str,
    source: Literal["output", "transcript", "input", "plan", "error"] = Query(default="output"),
    turn: int | None = Query(default=None, ge=0),
    cursor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=8192, ge=1, le=8 * 1024),
) -> dict[str, Any]:
    """Read one bounded ACP evidence source for a selected task and turn."""
    try:
        return acp_results.get_acp_result(
            storage_documents.get_default_document_store(),
            task_id,
            source=source,
            turn=turn,
            cursor=cursor,
            limit=limit,
            transcript_reader=acp_results.NativeClaudeTranscriptReader(),
        )
    except acp_results.ACPResultNotFound as exc:
        raise HTTPException(status_code=404, detail="ACP task result not found") from exc
    except acp_results.ACPResultRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/cron-preview")
async def preview_workflow_cron(
    expression: str,
    timezone: str | None = None,
    count: int = Query(default=3, ge=1, le=10),
) -> dict[str, Any]:
    """Next due times of a cron trigger, computed exactly as the backend clock fires them."""
    effective_timezone = timezone_name(
        timezone, preferences_store.load_preferences().get("timezone", "UTC")
    )
    error = cron_error(expression, effective_timezone)
    if error:
        raise HTTPException(status_code=400, detail=error)
    runs: list[str] = []
    cursor = utcnow()
    for _ in range(count):
        cursor = next_cron_time(expression, cursor, effective_timezone)
        runs.append(cursor.astimezone(UTC).isoformat().replace("+00:00", "Z"))
    return {"next_runs": runs}


@router.get("/workflows", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_workflows(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Return the workflow definition list for the workflows page/sidebar."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    items = workflows_service.list_workflow_list_items(
        storage_documents.get_default_document_store()
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOWS_SNAPSHOT,
        {"items": [item.model_dump(mode="json") for item in items]},
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WORKFLOWS_SNAPSHOT]
    )


@router.get(
    "/workflows/content", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_workflow_content(
    request: Request,
    path: str = Query(...),
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Return one stored workflow definition by id or mounted virtual path."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    try:
        definition = workflows_service.get_workflow_definition(
            storage_documents.get_default_document_store(), path
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    payload = workflows_runtime._workflow_content_response(definition)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_UPDATED,
        payload.model_dump(mode="json", by_alias=True),
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WORKFLOW_UPDATED]
    )


@router.put(
    "/workflows/content", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def put_workflow_content(
    body: workflows_models.WorkflowContentPutRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Persist one workflow definition."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    workflow_payload = dict(body.workflow)
    target_path = body.path
    if target_path:
        try:
            workflow_id = workflow_id_from_path(target_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if workflow_payload.get("id") and workflow_payload.get("id") != workflow_id:
            raise HTTPException(status_code=400, detail="Workflow id must match requested path")
        workflow_payload["id"] = workflow_id
    try:
        definition = workflows_service.save_workflow_definition(
            storage_documents.get_default_document_store(), workflow_payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = workflows_runtime._workflow_content_response(definition)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_UPDATED,
        payload.model_dump(mode="json", by_alias=True),
        request_id=request_id,
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOWS_SNAPSHOT,
        {
            "items": [
                item.model_dump(mode="json")
                for item in workflows_service.list_workflow_list_items(
                    storage_documents.get_default_document_store()
                )
            ]
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WORKFLOW_UPDATED, events_contracts.UiEvent.WORKFLOWS_SNAPSHOT],
    )


@router.post("/workflows", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def create_workflow(
    body: workflows_models.WorkflowCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Create a new workflow definition with the minimal valid scaffold."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    try:
        definition = workflows_service.save_workflow_definition(
            storage_documents.get_default_document_store(),
            default_workflow_definition(
                title=body.title.strip() or "Untitled workflow",
                description=body.description,
                document_type=body.document_type,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = workflows_runtime._workflow_content_response(definition)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_UPDATED,
        payload.model_dump(mode="json", by_alias=True),
        request_id=request_id,
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOWS_SNAPSHOT,
        {
            "items": [
                item.model_dump(mode="json")
                for item in workflows_service.list_workflow_list_items(
                    storage_documents.get_default_document_store()
                )
            ]
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WORKFLOW_UPDATED, events_contracts.UiEvent.WORKFLOWS_SNAPSHOT],
    )


@router.delete(
    "/workflows/{workflow_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def delete_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Delete one stored workflow definition."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    store = storage_documents.get_default_document_store()
    try:
        definition = workflows_service.get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflows_service.delete_workflow_definition(store, workflow_id)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_DELETED,
        {"workflow_id": workflow_id},
        request_id=request_id,
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOWS_SNAPSHOT,
        {
            "items": [
                item.model_dump(mode="json")
                for item in workflows_service.list_workflow_list_items(store)
            ]
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WORKFLOW_DELETED, events_contracts.UiEvent.WORKFLOWS_SNAPSHOT],
    )


@router.post(
    "/workflows/{workflow_id}/pause",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def pause_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Pause one workflow definition and any active run."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    store = storage_documents.get_default_document_store()
    try:
        definition = workflows_service.get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    updated = workflows_service.save_workflow_definition(
        store,
        definition.model_copy(
            update={"paused": True, "updated_at": utcnow().isoformat()}
        ).model_dump(mode="json", by_alias=True),
    )
    await workflows_runtime.get_workflow_executor().pause_workflow(updated.id)
    payload = workflows_runtime._workflow_content_response(updated)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_UPDATED,
        payload.model_dump(mode="json", by_alias=True),
        request_id=request_id,
    )
    workflows_runtime.broadcast_workflows_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WORKFLOW_UPDATED, events_contracts.UiEvent.WORKFLOWS_SNAPSHOT],
    )


@router.post(
    "/workflows/{workflow_id}/resume",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def resume_workflow(
    workflow_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Resume one paused workflow definition and its active run, if any."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    store = storage_documents.get_default_document_store()
    try:
        definition = workflows_service.get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    updated = workflows_service.save_workflow_definition(
        store,
        definition.model_copy(
            update={"paused": False, "updated_at": utcnow().isoformat()}
        ).model_dump(mode="json", by_alias=True),
    )
    await workflows_runtime.get_workflow_executor().resume_workflow(updated.id)
    payload = workflows_runtime._workflow_content_response(updated)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_UPDATED,
        payload.model_dump(mode="json", by_alias=True),
        request_id=request_id,
    )
    workflows_runtime.broadcast_workflows_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.WORKFLOW_UPDATED, events_contracts.UiEvent.WORKFLOWS_SNAPSHOT],
    )


@router.get(
    "/workflow-runs", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_workflow_runs(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Return active and recent workflow runs."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    items = workflows_service.list_workflow_runs(storage_documents.get_default_document_store())
    workflows_runtime.broadcast_workflow_runs_snapshot(items, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WORKFLOW_RUNS_SNAPSHOT]
    )


@router.post(
    "/workflows/{workflow_id}/trigger",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def trigger_workflow(
    workflow_id: str,
    body: workflows_models.WorkflowManualTriggerRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Manually trigger one workflow definition for operator testing."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    store = storage_documents.get_default_document_store()
    try:
        definition = workflows_service.get_workflow_definition(store, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if definition is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    try:
        trigger = workflows_service.build_manual_trigger_event(
            definition,
            payload=body.payload,
            preference_timezone=timezone_name(preferences_store.load_preferences().get("timezone")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    trigger = {**trigger, "request_id": request_id}
    run = await workflows_runtime.get_workflow_executor().trigger_workflow(definition.id, trigger)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail="Workflow trigger refused because the workflow is paused or disabled",
        )
    refreshed = workflows_service.get_workflow_run(store, run.run_id) or run
    run_payload = refreshed.model_dump(mode="json")
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_RUN_STARTED,
        {
            "workflow_id": workflow_id,
            "run_id": refreshed.run_id,
            "status": refreshed.status,
            "run": run_payload,
        },
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WORKFLOW_RUN_STARTED]
    )


@router.post(
    "/workflows/{workflow_id}/runs/{run_id}/approval",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def resolve_workflow_approval(
    workflow_id: str,
    run_id: str,
    body: workflows_models.WorkflowApprovalRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Approve or reject a paused workflow approval node."""

    request_id = http_requests.request_correlation_id(request, x_request_id)
    run = await workflows_runtime.get_workflow_executor().resolve_approval(
        workflow_id,
        run_id,
        approved=body.approved,
    )
    if run is None:
        raise HTTPException(status_code=409, detail="Workflow run is not awaiting approval")
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.WORKFLOW_RUN_UPDATED,
        {"workflow_id": workflow_id, "run_id": run_id, "run": run.model_dump(mode="json")},
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WORKFLOW_RUN_UPDATED]
    )


@router.post(
    "/actions/run", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def action_run(
    body: workflows_models.ActionRunRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Run a prompt/code action through the existing SSE message flow."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    try:
        thread_id, response_obj, response_text = await workflows_runtime._execute_action_run(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent action failed: {exc}") from exc
    if body.broadcast_response:
        events_stream.broadcast_ui_event(
            events_contracts.UiEvent.AGENT_MESSAGE,
            {
                "content": response_text,
                "metadata": {"widget_id": body.widget_id, "action": "run", "thread_id": thread_id},
                "timestamp": time.time(),
            },
            scope="chat",
            request_id=request_id,
        )
    schedules_service._broadcast_widget_refresh(
        monitoring_state.get_state_reader(), request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.WIDGETS_UPDATED]
    )


@router.post(
    "/actions/generate", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def action_generate(
    body: workflows_models.ActionGenerateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Generate output through the existing SSE message flow."""
    request_id = http_requests.request_correlation_id(request, x_request_id)

    if not lifecycle_state.client:
        raise HTTPException(status_code=503, detail="Client not initialized")

    prompt = body.prompt
    if body.input:
        prompt = f"Input:\n{body.input}\n\n{prompt}"
    if body.target:
        prompt = f"Target: {body.target}\n\n{prompt}"
    if body.widget_id:
        prompt = f"Widget ID: {body.widget_id}\n\n{prompt}"

    thread_id = workflows_runtime._resolve_action_thread_id(body.thread_id)
    metadata = workflows_runtime._build_action_metadata(
        thread_id=thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source,
        widget_id=body.widget_id,
        root_widget_id=body.root_widget_id,
        action=body.invocation_source or "workspace.generate",
    )
    chat_threads.upsert_thread(
        thread_id,
        invocation_type=body.invocation_type,
        invocation_source=body.invocation_source or "workspace.generate",
        root_widget_id=body.root_widget_id or body.widget_id,
        widget_id=body.widget_id,
    )

    try:
        response = await lifecycle_state.client.send_message(prompt, metadata=metadata)
    except Exception as exc:
        chat_threads.fail_thread(thread_id, str(exc), widget_id=body.widget_id)
        raise

    chat_threads.complete_thread(thread_id, response.response, widget_id=body.widget_id)
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.ACTION_GENERATE_SENT,
        {
            "thread_id": thread_id,
            "widget_id": body.widget_id,
            "response": response.response,
        },
        request_id=request_id,
    )
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.AGENT_MESSAGE,
        {
            "content": response.response,
            "metadata": {"widget_id": body.widget_id, "action": "generate", "thread_id": thread_id},
            "timestamp": time.time(),
        },
        scope="chat",
        request_id=request_id,
    )
    schedules_service._broadcast_widget_refresh(
        monitoring_state.get_state_reader(), request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id,
        [events_contracts.UiEvent.ACTION_GENERATE_SENT, events_contracts.UiEvent.WIDGETS_UPDATED],
    )
