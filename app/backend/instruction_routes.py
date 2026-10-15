"""Instructions API: list, read, edit, and revision history.

Every route acknowledges with 202 and publishes its result on /api/ui/stream.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from . import instruction_service
from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .request_utils import request_correlation_id as _request_correlation_id
from .ui_routes import broadcast_instructions_snapshot, broadcast_ui_event

router = APIRouter(prefix="/api/ui/instructions", tags=["instructions"])

_REQUEST_ID_HEADER = Header(default=None, alias="X-Request-ID")
_PATH_QUERY = Query(..., description="Instruction path, e.g. progenitor/genesis.md")


class InstructionUpdateRequest(BaseModel):
    path: str
    content: str


def _not_found(path: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Instruction '{path}' not found.")


@router.get("", status_code=202, response_model=UiRequestAcknowledgement)
async def list_instructions(
    request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> UiRequestAcknowledgement:
    """Publish every instruction's summary."""
    request_id = _request_correlation_id(request, x_request_id)
    instructions = await asyncio.to_thread(instruction_service.list_instructions)
    broadcast_instructions_snapshot(instructions, request_id=request_id)
    return acknowledgement_response(request_id, ["instructions.snapshot"])


@router.get("/content", status_code=202, response_model=UiRequestAcknowledgement)
async def get_instruction(
    request: Request, path: str = _PATH_QUERY, x_request_id: str | None = _REQUEST_ID_HEADER
) -> UiRequestAcknowledgement:
    """Publish one instruction with its content."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        instruction = await asyncio.to_thread(instruction_service.get_instruction, path)
    except FileNotFoundError as exc:
        raise _not_found(path) from exc
    broadcast_ui_event("instruction.loaded", instruction, request_id=request_id)
    return acknowledgement_response(request_id, ["instruction.loaded"])


@router.put("/content", status_code=202, response_model=UiRequestAcknowledgement)
async def update_instruction(
    body: InstructionUpdateRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Save a new version of an editable instruction."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        saved = await asyncio.to_thread(
            instruction_service.save_instruction, body.path, body.content
        )
    except FileNotFoundError as exc:
        raise _not_found(body.path) from exc
    except instruction_service.ReadOnlyInstructionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_ui_event("instruction.loaded", saved, request_id=request_id)
    broadcast_instructions_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["instruction.loaded", "instructions.snapshot"])


@router.get("/revisions", status_code=202, response_model=UiRequestAcknowledgement)
async def list_instruction_revisions(
    request: Request, path: str = _PATH_QUERY, x_request_id: str | None = _REQUEST_ID_HEADER
) -> UiRequestAcknowledgement:
    """Publish an instruction's captured revisions, newest first."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        revisions = await asyncio.to_thread(instruction_service.list_revisions, path)
    except FileNotFoundError as exc:
        raise _not_found(path) from exc
    broadcast_ui_event(
        "instruction.revisions.loaded",
        {"path": path, "revisions": revisions},
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["instruction.revisions.loaded"])


@router.get("/revisions/{revision_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def get_instruction_revision(
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Publish one revision's content."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        revision = await asyncio.to_thread(instruction_service.get_revision, path, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instruction revision not found.") from exc
    broadcast_ui_event("instruction.revision.loaded", revision, request_id=request_id)
    return acknowledgement_response(request_id, ["instruction.revision.loaded"])


@router.post(
    "/revisions/{revision_id}/restore",
    status_code=202,
    response_model=UiRequestAcknowledgement,
)
async def restore_instruction_revision(
    revision_id: str,
    request: Request,
    path: str = _PATH_QUERY,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> UiRequestAcknowledgement:
    """Make a captured revision the live instruction."""
    request_id = _request_correlation_id(request, x_request_id)
    try:
        restored = await asyncio.to_thread(instruction_service.restore_revision, path, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instruction revision not found.") from exc
    except instruction_service.ReadOnlyInstructionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    broadcast_ui_event("instruction.loaded", restored, request_id=request_id)
    broadcast_instructions_snapshot(request_id=request_id)
    return acknowledgement_response(request_id, ["instruction.loaded", "instructions.snapshot"])
