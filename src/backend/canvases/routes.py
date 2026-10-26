"""Event-stream-only UI routes for agent-built canvases."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
from src.backend.canvases.events import canvas_events
from src.backend.events.documents import list_document_types
from src.backend.http.requests import request_correlation_id as _request_correlation_id
from src.backend.storage.documents import (
    PROGENA_STATE_PREFIX,
    BackendDocumentStore,
    document_write_origin,
    get_default_document_store,
)
from src.progena.canvases.contracts import (
    CANVAS_MANIFEST_FILE,
    MAX_CANVAS_DATA_BYTES,
    CanvasValidationError,
    canvas_data_key,
    canvas_file_key,
    canvas_revision,
    canvas_status_key,
    split_canvas_file_key,
    validate_canvas_data_key,
    validate_canvas_manifest,
    validate_canvas_name,
)
from src.progena.documents.types import DOCUMENT_OPERATIONS, DocumentType

router = APIRouter(prefix="/api/ui", tags=["canvases"])
_REQUEST_ID_HEADER = Header(default=None, alias="X-Operation-Id")


class CanvasSummary(BaseModel):
    name: str
    title: str
    description: str
    entry: str
    libraries: list[str]
    artifacts: list[str]
    files: list[str]
    revision: str
    updated_at: str | None
    manifest_error: str | None
    status: dict[str, Any] | None


class CanvasStatusError(BaseModel):
    message: str
    stack: str | None = None


class CanvasStatusRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    state: Literal["loaded", "error"]
    revision: str
    errors: list[CanvasStatusError] = Field(default_factory=list)


class CanvasDocumentsRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    type: str
    id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)
    prefix: str = Field(default="", max_length=512)
    cursor: str | None = None


class CanvasDataRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    op: Literal["get", "set", "delete", "list"]
    key: str | None = None
    value: Any = None


class CanvasSubscriptionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    events: list[str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canvas_rows(store: BackendDocumentStore, name: str) -> list[tuple[str, Any]]:
    prefix = canvas_file_key(name, "").removesuffix("/") + "/"
    rows: list[tuple[str, Any]] = []
    for key, document in store.list_documents(PROGENA_STATE_PREFIX, prefix):
        split = split_canvas_file_key(key)
        if split is None or split[0] != name or not isinstance(document.payload, dict):
            continue
        content = document.payload.get("content")
        if isinstance(content, str):
            rows.append((split[1], document))
    return rows


def _canvas_files(store: BackendDocumentStore, name: str) -> dict[str, str]:
    return {path: document.payload["content"] for path, document in _canvas_rows(store, name)}


def _canvas_known(store: BackendDocumentStore, name: str) -> bool:
    return bool(_canvas_rows(store, name)) or bool(store.list_canvas_revisions(name, limit=1))


def _require_canvas(store: BackendDocumentStore, name: str) -> str:
    try:
        name = validate_canvas_name(name)
    except CanvasValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _canvas_known(store, name):
        raise HTTPException(status_code=404, detail=f"Canvas '{name}' not found.")
    return name


def _summary(store: BackendDocumentStore, name: str) -> CanvasSummary:
    files = _canvas_files(store, name)
    manifest_error: str | None = None
    manifest: dict[str, Any] = {}
    content = files.get(CANVAS_MANIFEST_FILE)
    if content is None:
        manifest_error = "manifest.json is missing."
    else:
        try:
            manifest = validate_canvas_manifest(name, content)
        except CanvasValidationError as exc:
            manifest_error = str(exc)
    rows = _canvas_rows(store, name)
    timestamps = [
        str(document.payload.get("updated_at") or document.updated_at or "")
        for _path, document in rows
    ]
    updated_at = max((timestamp for timestamp in timestamps if timestamp), default=None)
    status_document = store.get_document(PROGENA_STATE_PREFIX, canvas_status_key(name), None)
    status = (
        status_document.payload
        if status_document and isinstance(status_document.payload, dict)
        else None
    )
    return CanvasSummary(
        name=name,
        title=str(manifest.get("title") or name),
        description=str(manifest.get("description") or ""),
        entry=str(manifest.get("entry") or ""),
        libraries=[str(item) for item in manifest.get("libraries", [])],
        artifacts=[str(item) for item in manifest.get("artifacts", [])],
        files=sorted(files),
        revision=canvas_revision(files),
        updated_at=updated_at,
        manifest_error=manifest_error,
        status=status,
    )


def canvas_summaries(store: BackendDocumentStore | None = None) -> list[CanvasSummary]:
    """Return every currently live canvas's presentation summary."""

    store = store or get_default_document_store()
    names: set[str] = set()
    for key, _document in store.list_documents(PROGENA_STATE_PREFIX, "canvas:"):
        split = split_canvas_file_key(key)
        if split is not None:
            names.add(split[0])
    return [_summary(store, name) for name in sorted(names)]


def _validate_pattern(pattern: str, types: dict[str, DocumentType]) -> str:
    document_type, separator, operation = pattern.partition(".")
    if not separator or "." in operation or not document_type or not operation:
        raise HTTPException(status_code=400, detail=f"Invalid document event pattern '{pattern}'.")
    if document_type != "*" and document_type not in types:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{document_type}'.")
    if operation != "*" and operation not in DOCUMENT_OPERATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid document event pattern '{pattern}'.")
    return pattern


@router.get("/canvases", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_canvases(
    request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    request_id = _request_correlation_id(request, x_request_id)

    broadcast_canvases_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["canvases.snapshot"])


@router.get(
    "/canvases/{name}/files",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_canvas_files(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    files = await asyncio.to_thread(_canvas_files, store, name)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.files.loaded",
        {"name": name, "revision": canvas_revision(files), "files": files},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.files.loaded"])


@router.delete(
    "/canvases/{name}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def delete_canvas(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    del client_id
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    files = await asyncio.to_thread(_canvas_files, store, name)
    with document_write_origin("user"):
        for path in files:
            store.delete_document(PROGENA_STATE_PREFIX, canvas_file_key(name, path))
        store.delete_document(PROGENA_STATE_PREFIX, canvas_status_key(name))
        data_prefix = canvas_data_key(name, "")
        for key, _document in store.list_documents(PROGENA_STATE_PREFIX, data_prefix):
            store.delete_document(PROGENA_STATE_PREFIX, key)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event("canvas.deleted", {"name": name}, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["canvas.deleted"])


@router.post(
    "/canvases/{name}/status",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def save_canvas_status(
    name: str,
    body: CanvasStatusRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    payload = {
        "state": body.state,
        "revision": body.revision,
        "errors": [item.model_dump(exclude_none=True) for item in body.errors],
        "reported_at": _utc_now(),
    }
    with document_write_origin("user"):
        store.save_document(PROGENA_STATE_PREFIX, canvas_status_key(name), payload)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.status.saved",
        {"name": name, "state": body.state, "revision": body.revision},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.status.saved"])


@router.post(
    "/canvases/{name}/documents",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_canvas_documents(
    name: str,
    body: CanvasDocumentsRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    types = await asyncio.to_thread(list_document_types, store)
    document_type = types.get(body.type)
    if document_type is None:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{body.type}'.")
    has_more = False
    if document_type.key is not None:
        rows = [
            (
                document_type.key,
                store.get_document(PROGENA_STATE_PREFIX, document_type.key, None),
            )
        ]
    elif body.id is not None:
        key = document_type.document_key(body.id)
        rows = [(key, store.get_document(PROGENA_STATE_PREFIX, key, None))]
    else:
        rows = store.list_documents(
            PROGENA_STATE_PREFIX, (document_type.key_prefix or "") + body.prefix
        )
        if body.cursor is not None:
            rows = [item for item in rows if item[0] > body.cursor]
        has_more = len(rows) > body.limit
        rows = rows[: body.limit]
    items = [
        {"key": key, "id": document_type.document_id(key), "document": document.payload}
        for key, document in rows
        if document is not None
    ]
    # Next-page cursor: the last key returned, or None once the listing is exhausted.
    cursor = items[-1]["key"] if has_more and items else None
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.documents.loaded",
        {"name": name, "type": body.type, "items": items, "cursor": cursor},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.documents.loaded"])


@router.post(
    "/canvases/{name}/data", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def canvas_data(
    name: str,
    body: CanvasDataRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    key = body.key
    if body.op != "list":
        try:
            key = validate_canvas_data_key(key or "")
        except CanvasValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    value: Any = None
    items: list[dict[str, Any]] | None = None
    if body.op == "list":
        prefix = canvas_data_key(name, "")
        items = [
            {"key": item_key.removeprefix(prefix), "value": document.payload.get("value")}
            for item_key, document in store.list_documents(PROGENA_STATE_PREFIX, prefix)
            if isinstance(document.payload, dict)
        ]
    else:
        assert key is not None
        store_key = canvas_data_key(name, key)
        if body.op == "get":
            document = store.get_document(PROGENA_STATE_PREFIX, store_key, None)
            value = (
                document.payload.get("value")
                if document and isinstance(document.payload, dict)
                else None
            )
        elif body.op == "set":
            try:
                size = len(
                    json.dumps(body.value, ensure_ascii=False, separators=(",", ":")).encode()
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="Canvas data value must be JSON serializable."
                ) from exc
            if size > MAX_CANVAS_DATA_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Canvas data value is {size} bytes; the limit is {MAX_CANVAS_DATA_BYTES}.",
                )
            with document_write_origin("user"):
                store.save_document(
                    PROGENA_STATE_PREFIX,
                    store_key,
                    {"value": body.value, "updated_at": _utc_now()},
                )
            value = body.value
        else:
            with document_write_origin("user"):
                store.delete_document(PROGENA_STATE_PREFIX, store_key)
    payload: dict[str, Any] = {"name": name, "op": body.op, "key": key, "value": value}
    if items is not None:
        payload["items"] = items
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.data.loaded", payload, request_id=request_id, client_id=body.client_id
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.data.loaded"])


@router.put(
    "/canvases/{name}/subscription",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def update_canvas_subscription(
    name: str,
    body: CanvasSubscriptionRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    types = await asyncio.to_thread(list_document_types, store)
    events = [_validate_pattern(pattern, types) for pattern in body.events]
    canvas_events.set_interest(body.client_id, name, events)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.subscription.updated",
        {"name": name, "events": events},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.subscription.updated"])


@router.delete(
    "/canvases/{name}/subscription",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def delete_canvas_subscription(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_canvas, store, name)
    canvas_events.clear_interest(client_id, name)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "canvas.subscription.updated",
        {"name": name, "events": []},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["canvas.subscription.updated"])


def broadcast_canvases_snapshot(
    *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the live summary of every agent-built canvas."""

    from src.backend.canvases.routes import canvas_summaries

    items = [summary.model_dump(mode="json") for summary in canvas_summaries()]
    return events_stream.broadcast_ui_event(
        "canvases.snapshot", {"items": items}, request_id=request_id
    )
