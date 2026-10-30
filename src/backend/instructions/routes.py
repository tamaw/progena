"""Instructions API: list, read, edit, and revision history.

Every route acknowledges with 202 and publishes its result on /api/ui/stream.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.instructions import service as instruction_service

router = APIRouter(prefix="/api/ui/instructions", tags=["instructions"])

_REQUEST_ID_HEADER = Header(default=None, alias="X-Operation-Id")
_PATH_QUERY = Query(..., description="Instruction path, e.g. progena/genesis.md")


class InstructionUpdateRequest(BaseModel):
    path: str
    content: str


def _not_found(path: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Instruction '{path}' not found.")


@router.get("", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def list_instructions(
    request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    """Publish every instruction's summary."""
    request_id = _request_correlation_id(request, x_request_id)
    instructions = await asyncio.to_thread(instruction_service.list_instructions)
    broadcast_instructions_snapshot(instructions, request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.INSTRUCTIONS_SNAPSHOT]
    )


@router.get("/content", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_instruction(
    request: Request, path: str = _PATH_QUERY, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    """Publish one instruction with its content."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        instruction = await asyncio.to_thread(instruction_service.get_instruction, path)
    except FileNotFoundError as exc:
        raise _not_found(path) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTION_LOADED, instruction, request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.INSTRUCTION_LOADED]
    )


@router.put("/content", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def update_instruction(
    body: InstructionUpdateRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Save a new version of an editable instruction."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        saved = await asyncio.to_thread(
            instruction_service.save_instruction, body.path, body.content
        )
    except FileNotFoundError as exc:
        raise _not_found(body.path) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTION_LOADED, saved, request_id=request_id
    )
    broadcast_instructions_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id,
        [
            events_contracts.UiEvent.INSTRUCTION_LOADED,
            events_contracts.UiEvent.INSTRUCTIONS_SNAPSHOT,
        ],
    )


@router.get("/revisions", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def list_instruction_revisions(
    request: Request, path: str = _PATH_QUERY, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    """Publish an instruction's captured revisions, newest first."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        revisions = await asyncio.to_thread(instruction_service.list_revisions, path)
    except FileNotFoundError as exc:
        raise _not_found(path) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTION_REVISIONS_LOADED,
        {"path": path, "revisions": revisions},
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.INSTRUCTION_REVISIONS_LOADED]
    )


@router.get(
    "/revisions/{revision_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_instruction_revision(
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Publish one revision's content."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        revision = await asyncio.to_thread(instruction_service.get_revision, path, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instruction revision not found.") from exc
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTION_REVISION_LOADED, revision, request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.INSTRUCTION_REVISION_LOADED]
    )


@router.post(
    "/revisions/{revision_id}/restore",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def restore_instruction_revision(
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    """Make a captured revision the live instruction."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        restored = await asyncio.to_thread(instruction_service.restore_revision, path, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instruction revision not found.") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTION_LOADED, restored, request_id=request_id
    )
    broadcast_instructions_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(
        request_id,
        [
            events_contracts.UiEvent.INSTRUCTION_LOADED,
            events_contracts.UiEvent.INSTRUCTIONS_SNAPSHOT,
        ],
    )


def broadcast_instructions_snapshot(
    instructions: list[dict[str, Any]] | None = None, *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast every instruction's summary to /api/ui/stream subscribers.

    Emitted after the Instructions page requests the list, after edits and
    restores, and whenever an ``instruction:`` document changes (runtime boot
    sync surfaced via the Postgres NOTIFY watcher).
    """
    payload = instruction_service.list_instructions() if instructions is None else instructions
    return events_stream.broadcast_ui_event(
        events_contracts.UiEvent.INSTRUCTIONS_SNAPSHOT,
        {"instructions": payload},
        request_id=request_id,
    )
