"""Acknowledged UI routes for the versioned routine registry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.backend.chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from app.backend.document_store import get_default_document_store
from app.backend.request_utils import request_correlation_id
from app.backend.routine_service import (
    get_routine,
    get_routine_revision,
    list_routines,
    save_routine,
)

router = APIRouter(prefix="/api/ui", tags=["routines"])


class RoutineSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine: dict[str, Any]
    publish: bool = False


def _request_id(request: Request, supplied: str | None) -> str:
    return request_correlation_id(request, supplied)


@router.get("/routines", status_code=202, response_model=UiRequestAcknowledgement)
async def get_routines(
    request: Request, x_request_id: str | None = Header(default=None, alias="X-Request-ID")
) -> UiRequestAcknowledgement:
    request_id = _request_id(request, x_request_id)
    from .ui_routes import broadcast_ui_event

    routines = [
        item.model_dump(mode="json") for item in list_routines(get_default_document_store())
    ]
    broadcast_ui_event("routines.snapshot", {"items": routines}, request_id=request_id)
    return acknowledgement_response(request_id, ["routines.snapshot"])


@router.get("/routines/{routine_id}", status_code=202, response_model=UiRequestAcknowledgement)
async def get_routine_detail(
    routine_id: str,
    request: Request,
    revision: int | None = None,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    request_id = _request_id(request, x_request_id)
    from .ui_routes import broadcast_ui_event

    store = get_default_document_store()
    routine = get_routine(store, routine_id)
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine not found")
    resolved_revision = revision or routine.published_revision
    published = (
        get_routine_revision(store, routine_id, resolved_revision) if resolved_revision else None
    )
    broadcast_ui_event(
        "routine.detail",
        {
            "routine": routine.model_dump(mode="json"),
            "revision": published.model_dump(mode="json") if published else None,
        },
        request_id=request_id,
    )
    return acknowledgement_response(request_id, ["routine.detail"])


@router.put("/routines", status_code=202, response_model=UiRequestAcknowledgement)
async def put_routine(
    body: RoutineSaveRequest,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> UiRequestAcknowledgement:
    request_id = _request_id(request, x_request_id)
    from .ui_routes import broadcast_ui_event

    routine = save_routine(get_default_document_store(), body.routine, publish=body.publish)
    broadcast_ui_event(
        "routine.updated", {"routine": routine.model_dump(mode="json")}, request_id=request_id
    )
    return acknowledgement_response(request_id, ["routine.updated"])
