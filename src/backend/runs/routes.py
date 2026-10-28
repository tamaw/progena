"""Acknowledged browser routes for the Runs read model."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request

from src.backend.events import stream
from src.backend.http import contracts as http_contracts
from src.backend.http.requests import request_correlation_id
from src.backend.storage.documents import get_default_document_store

from .events import runs_events
from .models import RunKind, RunsSubscription
from .service import list_runs

router = APIRouter(tags=["runs"])


def _require_stream(client_id: str) -> None:
    if client_id not in stream._ui_stream_clients.values():
        raise HTTPException(status_code=409, detail="An active UI stream is required")


@router.get("/runs", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_runs(
    request: Request,
    kind: RunKind | None = Query(default=None),
    status: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    cursor: str | None = Query(default=None, max_length=256),
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> http_contracts.UiRequestAcknowledgement:
    request_id = request_correlation_id(request, x_request_id)
    runs, next_cursor = await list_runs(
        get_default_document_store(), kind=kind, status=status, q=q, cursor=cursor
    )
    stream.broadcast_ui_event(
        "runs.snapshot",
        {"runs": [row.model_dump(mode="json") for row in runs], "next_cursor": next_cursor},
        request_id=request_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["runs.snapshot"])


@router.get(
    "/runs/{kind}/{run_id}/trace",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_run_trace(
    kind: RunKind,
    run_id: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> http_contracts.UiRequestAcknowledgement:
    _require_stream(client_id)
    request_id = request_correlation_id(request, x_request_id)
    trace, _threads = await runs_events.read_trace(get_default_document_store(), kind, run_id)
    stream.broadcast_ui_event(
        "run.trace.loaded",
        {"trace": trace.model_dump(mode="json")},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["run.trace.loaded"])


@router.put(
    "/runs/subscription", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def put_runs_subscription(
    body: RunsSubscription,
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
) -> http_contracts.UiRequestAcknowledgement:
    _require_stream(body.client_id)
    request_id = request_correlation_id(request, x_request_id)
    if (body.kind is None) != (body.run_id is None):
        raise HTTPException(status_code=422, detail="kind and run_id must be supplied together")
    event_types = ["run.subscription.updated"]
    payload = {"kind": body.kind, "run_id": body.run_id}
    if body.selected():
        assert body.kind is not None and body.run_id is not None
        selected = await runs_events.subscribe(
            get_default_document_store(), body.client_id, body.kind, body.run_id
        )
        stream.broadcast_ui_event(
            "run.subscription.updated", payload, request_id=request_id, client_id=body.client_id
        )
        if selected is not None:
            trace, token = selected
            stream.broadcast_ui_event(
                "run.trace.updated",
                {"trace": trace.model_dump(mode="json")},
                request_id=request_id,
                client_id=body.client_id,
                interest=token,
            )
            event_types.append("run.trace.updated")
    else:
        runs_events.unsubscribe(body.client_id)
        stream.broadcast_ui_event(
            "run.subscription.updated", payload, request_id=request_id, client_id=body.client_id
        )
    return http_contracts.acknowledgement_response(request_id, event_types)
