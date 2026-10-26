"""Tasks routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.monitoring.state as monitoring_state
import src.backend.tasks.models as tasks_models
import src.backend.tasks.service as tasks_service
from src.progena.utils import utcnow

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/kanban", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_kanban_board(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Get the persisted kanban board used by the frontend."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    board = tasks_service._read_kanban_board(state_reader)
    response = tasks_service._kanban_board_response(state_reader, board)
    tasks_service.broadcast_kanban_snapshot(response, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["kanban.snapshot"])


@router.post(
    "/kanban/cards", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def create_kanban_card(
    body: tasks_models.KanbanCardCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Create a new kanban card in the backlog column."""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Card title is required")
    request_id = http_requests.request_correlation_id(request, x_request_id)

    state_reader = monitoring_state.get_state_reader()
    board = tasks_service._read_kanban_board(state_reader)
    column_map = {column["id"]: column for column in board["columns"]}
    now = utcnow().isoformat()
    card = tasks_service._normalize_kanban_card(
        {
            "id": f"card_{uuid.uuid4().hex[:12]}",
            "title": title,
            "summary": body.summary,
            "description": body.description,
            "column_id": "backlog",
            "status": "draft",
            "agent_name": column_map["backlog"]["agent_name"],
            "owner": body.owner,
            "project_id": body.project_id,
            "labels": body.labels,
            "created_at": now,
            "updated_at": now,
            "position": sum(1 for item in board["cards"] if item["column_id"] == "backlog"),
        },
        column_map,
    )
    board["cards"].append(card)
    board = tasks_service._write_kanban_board(state_reader, board)
    tasks_service.broadcast_kanban_snapshot(
        tasks_service._kanban_board_response(state_reader, board), request_id=request_id
    )
    return http_contracts.acknowledgement_response(request_id, ["kanban.snapshot"])


@router.patch(
    "/kanban/cards/{card_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def patch_kanban_card(
    card_id: str,
    body: tasks_models.KanbanCardPatchRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Patch an existing kanban card."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    board = tasks_service._read_kanban_board(state_reader)
    column_map = {column["id"]: column for column in board["columns"]}
    allowed_statuses = {status.id for status in tasks_service._kanban_status_options()}
    now = utcnow().isoformat()

    updated_card = None
    for card in board["cards"]:
        if card["id"] != card_id:
            continue

        if body.title is not None:
            title = body.title.strip()
            if not title:
                raise HTTPException(status_code=400, detail="Card title is required")
            card["title"] = title
        if body.summary is not None:
            card["summary"] = body.summary.strip()
        if body.description is not None:
            card["description"] = body.description
        if body.column_id is not None:
            if body.column_id not in column_map:
                raise HTTPException(
                    status_code=400, detail=f"Unknown kanban column: {body.column_id}"
                )
            card["column_id"] = body.column_id
            if body.agent_name is None:
                card["agent_name"] = column_map[body.column_id]["agent_name"]
        if body.status is not None:
            status = body.status.strip().lower()
            if status not in allowed_statuses:
                raise HTTPException(status_code=400, detail=f"Unknown kanban status: {body.status}")
            card["status"] = status
            if status == "running" and not card.get("started_at"):
                card["started_at"] = now
            if status == "complete":
                card["completed_at"] = body.completed_at or card.get("completed_at") or now
        if body.agent_name is not None:
            card["agent_name"] = body.agent_name.strip()
        if body.owner is not None:
            card["owner"] = body.owner.strip() or "user"
        if body.project_id is not None:
            card["project_id"] = body.project_id.strip() or None
        if body.labels is not None:
            card["labels"] = tasks_service._normalize_string_list(body.labels)
        if body.spec_path is not None:
            card["spec_path"] = body.spec_path.strip() or None
        if body.branch_name is not None:
            card["branch_name"] = body.branch_name.strip() or None
        if body.repo_id is not None:
            card["repo_id"] = body.repo_id.strip() or None
        if body.acp_task_id is not None:
            card["acp_task_id"] = body.acp_task_id.strip() or None
        if body.pr_url is not None:
            card["pr_url"] = body.pr_url.strip() or None
        if body.started_at is not None:
            card["started_at"] = body.started_at.strip() or None
        if body.completed_at is not None:
            card["completed_at"] = body.completed_at.strip() or None
        if body.last_error is not None:
            card["last_error"] = body.last_error.strip() or None
        if body.metadata is not None:
            card["metadata"] = body.metadata if isinstance(body.metadata, dict) else {}

        card["updated_at"] = now
        updated_card = tasks_service._normalize_kanban_card(card, column_map)
        card.update(updated_card)
        break

    if updated_card is None:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    target_position = body.position if body.position is not None else updated_card["position"]
    board["cards"] = tasks_service._reorder_kanban_cards(
        board["cards"],
        board["columns"],
        card_id,
        updated_card["column_id"],
        target_position,
    )
    board = tasks_service._write_kanban_board(state_reader, board)
    tasks_service.broadcast_kanban_snapshot(
        tasks_service._kanban_board_response(state_reader, board), request_id=request_id
    )
    return http_contracts.acknowledgement_response(request_id, ["kanban.snapshot"])


@router.delete(
    "/kanban/cards/{card_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def delete_kanban_card(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Delete an existing kanban card."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    board = tasks_service._read_kanban_board(state_reader)
    original_count = len(board["cards"])
    board["cards"] = [card for card in board["cards"] if card["id"] != card_id]
    if len(board["cards"]) == original_count:
        raise HTTPException(status_code=404, detail="Kanban card not found")

    board = tasks_service._write_kanban_board(state_reader, board)
    tasks_service.broadcast_kanban_snapshot(
        tasks_service._kanban_board_response(state_reader, board), request_id=request_id
    )
    return http_contracts.acknowledgement_response(request_id, ["kanban.snapshot"])


@router.get(
    "/tasks/{card_id}/activity",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_task_activity(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Acknowledge a task-activity request and publish its canonical timeline."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    tasks_service._find_kanban_card(state_reader, card_id)
    activity = tasks_service._task_activity_response(state_reader, card_id)
    tasks_service.broadcast_task_activity(card_id, activity, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["task.activity.updated"])


@router.get(
    "/tasks/{card_id}/content",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_task_content(
    card_id: str,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Acknowledge a task-content request and publish its canonical markdown."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    card = tasks_service._find_kanban_card(state_reader, card_id)
    content = tasks_service._read_task_content(state_reader, card)
    tasks_service.broadcast_task_content(card_id, content, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["task.content.updated"])


@router.put(
    "/tasks/{card_id}/content",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def update_task_content(
    card_id: str,
    body: tasks_models.TaskContentUpdateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Persist the markdown body for a single task card."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    tasks_service._find_kanban_card(state_reader, card_id)
    result = tasks_service._write_task_content(state_reader, card_id, body.content)
    tasks_service._append_task_activity_entry(
        state_reader,
        card_id=card_id,
        kind="content",
        actor="user",
        message="Updated the task brief.",
        metadata={"path": result.path},
        created_at=utcnow().isoformat(),
    )
    tasks_service.broadcast_task_content(card_id, result, request_id=request_id)
    tasks_service.broadcast_task_activity(card_id, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, ["task.content.updated", "task.activity.updated"]
    )


@router.post(
    "/tasks/{card_id}/activity",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def create_task_activity(
    card_id: str,
    body: tasks_models.TaskActivityCreateRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
):
    """Append a user-visible activity entry for a task card."""
    request_id = http_requests.request_correlation_id(request, x_request_id)
    state_reader = monitoring_state.get_state_reader()
    tasks_service._find_kanban_card(state_reader, card_id)
    tasks_service._append_task_activity_entry(
        state_reader,
        card_id=card_id,
        kind=body.kind,
        actor=body.actor,
        message=body.message,
        metadata=body.metadata,
        created_at=utcnow().isoformat(),
    )
    tasks_service.broadcast_task_activity(card_id, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["task.activity.updated"])
