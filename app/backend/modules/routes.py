"""Event-stream-only UI routes for agent-built modules."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

import app.backend.events.contracts as events_contracts
import app.backend.events.stream as events_stream
import app.backend.http.contracts as http_contracts
from app.backend.events.documents import list_document_types
from app.backend.http.requests import request_correlation_id as _request_correlation_id
from app.backend.modules.events import module_events
from app.backend.storage.documents import (
    PROGENITOR_STATE_PREFIX,
    BackendDocumentStore,
    ModuleRevision,
    document_write_origin,
    get_default_document_store,
)
from app.progenitor.document_types import DOCUMENT_OPERATIONS, DocumentType
from app.progenitor.modules import (
    MAX_MODULE_DATA_BYTES,
    MODULE_MANIFEST_FILE,
    ModuleValidationError,
    module_data_key,
    module_file_key,
    module_revision,
    module_status_key,
    split_module_file_key,
    validate_module_data_key,
    validate_module_manifest,
    validate_module_name,
)

router = APIRouter(prefix="/api/ui", tags=["modules"])
_REQUEST_ID_HEADER = Header(default=None, alias="X-Request-ID")


class ModuleSummary(BaseModel):
    name: str
    title: str
    description: str
    entry: str
    libraries: list[str]
    files: list[str]
    revision: str
    updated_at: str | None
    manifest_error: str | None
    status: dict[str, Any] | None


class ModuleRestoreRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    captured_at: str


class ModuleStatusError(BaseModel):
    message: str
    stack: str | None = None


class ModuleStatusRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    state: Literal["loaded", "error"]
    revision: str
    errors: list[ModuleStatusError] = Field(default_factory=list)


class ModuleDocumentsRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    type: str
    id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None


class ModuleDataRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    op: Literal["get", "set", "delete", "list"]
    key: str | None = None
    value: Any = None


class ModuleSubscriptionRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    events: list[str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _module_rows(store: BackendDocumentStore, name: str) -> list[tuple[str, Any]]:
    prefix = module_file_key(name, "").removesuffix("/") + "/"
    rows: list[tuple[str, Any]] = []
    for key, document in store.list_documents(PROGENITOR_STATE_PREFIX, prefix):
        split = split_module_file_key(key)
        if split is None or split[0] != name or not isinstance(document.payload, dict):
            continue
        content = document.payload.get("content")
        if isinstance(content, str):
            rows.append((split[1], document))
    return rows


def _module_files(store: BackendDocumentStore, name: str) -> dict[str, str]:
    return {path: document.payload["content"] for path, document in _module_rows(store, name)}


def _module_known(store: BackendDocumentStore, name: str) -> bool:
    return bool(_module_rows(store, name)) or bool(store.list_module_revisions(name, limit=1))


def _require_module(store: BackendDocumentStore, name: str) -> str:
    try:
        name = validate_module_name(name)
    except ModuleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not _module_known(store, name):
        raise HTTPException(status_code=404, detail=f"Module '{name}' not found.")
    return name


def _summary(store: BackendDocumentStore, name: str) -> ModuleSummary:
    files = _module_files(store, name)
    manifest_error: str | None = None
    manifest: dict[str, Any] = {}
    content = files.get(MODULE_MANIFEST_FILE)
    if content is None:
        manifest_error = "manifest.json is missing."
    else:
        try:
            manifest = validate_module_manifest(name, content)
        except ModuleValidationError as exc:
            manifest_error = str(exc)
    rows = _module_rows(store, name)
    timestamps = [
        str(document.payload.get("updated_at") or document.updated_at or "")
        for _path, document in rows
    ]
    updated_at = max((timestamp for timestamp in timestamps if timestamp), default=None)
    status_document = store.get_document(PROGENITOR_STATE_PREFIX, module_status_key(name), None)
    status = (
        status_document.payload
        if status_document and isinstance(status_document.payload, dict)
        else None
    )
    return ModuleSummary(
        name=name,
        title=str(manifest.get("title") or name),
        description=str(manifest.get("description") or ""),
        entry=str(manifest.get("entry") or ""),
        libraries=[str(item) for item in manifest.get("libraries", [])],
        files=sorted(files),
        revision=module_revision(files),
        updated_at=updated_at,
        manifest_error=manifest_error,
        status=status,
    )


def module_summaries(store: BackendDocumentStore | None = None) -> list[ModuleSummary]:
    """Return every currently live module's presentation summary."""

    store = store or get_default_document_store()
    names: set[str] = set()
    for key, _document in store.list_documents(PROGENITOR_STATE_PREFIX, "module:"):
        split = split_module_file_key(key)
        if split is not None:
            names.add(split[0])
    return [_summary(store, name) for name in sorted(names)]


def _revision_payload(revision: ModuleRevision, *, include_content: bool) -> dict[str, Any]:
    payload = {
        "id": revision.id,
        "path": revision.path,
        "op": revision.op,
        "content_hash": revision.content_hash,
        "captured_at": revision.captured_at,
    }
    if include_content:
        payload["content"] = revision.content
    return payload


def _parse_captured_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="captured_at must be an ISO-8601 timestamp"
        ) from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _validate_pattern(pattern: str, types: dict[str, DocumentType]) -> str:
    document_type, separator, operation = pattern.partition(".")
    if not separator or "." in operation or not document_type or not operation:
        raise HTTPException(status_code=400, detail=f"Invalid document event pattern '{pattern}'.")
    if document_type != "*" and document_type not in types:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{document_type}'.")
    if operation != "*" and operation not in DOCUMENT_OPERATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid document event pattern '{pattern}'.")
    return pattern


@router.get("/modules", status_code=202, response_model=http_contracts.UiRequestAcknowledgement)
async def get_modules(
    request: Request, x_request_id: str | None = _REQUEST_ID_HEADER
) -> http_contracts.UiRequestAcknowledgement:
    request_id = _request_correlation_id(request, x_request_id)

    broadcast_modules_snapshot(request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["modules.snapshot"])


@router.get(
    "/modules/{name}/files", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_module_files(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    files = await asyncio.to_thread(_module_files, store, name)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.files.loaded",
        {"name": name, "revision": module_revision(files), "files": files},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.files.loaded"])


@router.get(
    "/modules/{name}/revisions",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_module_revisions(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    revisions = await asyncio.to_thread(store.list_module_revisions, name)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.revisions.loaded",
        {
            "name": name,
            "revisions": [_revision_payload(item, include_content=False) for item in revisions],
        },
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.revisions.loaded"])


@router.get(
    "/modules/{name}/revisions/{revision_id}",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_module_revision(
    name: str,
    revision_id: int,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    revision = await asyncio.to_thread(store.get_module_revision, name, revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="Module revision not found.")
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.revision.loaded",
        {"name": name, "revision": _revision_payload(revision, include_content=True)},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.revision.loaded"])


@router.post(
    "/modules/{name}/restore",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def restore_module(
    name: str,
    body: ModuleRestoreRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    captured_at = _parse_captured_at(body.captured_at)
    target = await asyncio.to_thread(store.module_state_at, name, captured_at)
    revisions = await asyncio.to_thread(store.list_module_revisions, name, limit=1_000_000)
    if not target and not any(
        revision.captured_at and _parse_captured_at(revision.captured_at) <= captured_at
        for revision in revisions
    ):
        raise HTTPException(status_code=404, detail="No module state exists at captured_at.")
    current = await asyncio.to_thread(_module_files, store, name)
    with document_write_origin("user"):
        for path, content in target.items():
            store.save_document(
                PROGENITOR_STATE_PREFIX,
                module_file_key(name, path),
                {"content": content, "updated_at": _utc_now()},
            )
        for path in set(current) - set(target):
            store.delete_document(PROGENITOR_STATE_PREFIX, module_file_key(name, path))
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.restored",
        {"name": name, "captured_at": body.captured_at, "revision": module_revision(target)},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.restored"])


@router.delete(
    "/modules/{name}", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def delete_module(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    del client_id
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    files = await asyncio.to_thread(_module_files, store, name)
    with document_write_origin("user"):
        for path in files:
            store.delete_document(PROGENITOR_STATE_PREFIX, module_file_key(name, path))
        store.delete_document(PROGENITOR_STATE_PREFIX, module_status_key(name))
        data_prefix = module_data_key(name, "")
        for key, _document in store.list_documents(PROGENITOR_STATE_PREFIX, data_prefix):
            store.delete_document(PROGENITOR_STATE_PREFIX, key)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event("module.deleted", {"name": name}, request_id=request_id)
    return http_contracts.acknowledgement_response(request_id, ["module.deleted"])


@router.post(
    "/modules/{name}/status",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def save_module_status(
    name: str,
    body: ModuleStatusRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    payload = {
        "state": body.state,
        "revision": body.revision,
        "errors": [item.model_dump(exclude_none=True) for item in body.errors],
        "reported_at": _utc_now(),
    }
    with document_write_origin("user"):
        store.save_document(PROGENITOR_STATE_PREFIX, module_status_key(name), payload)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.status.saved",
        {"name": name, "state": body.state, "revision": body.revision},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.status.saved"])


@router.post(
    "/modules/{name}/documents",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def get_module_documents(
    name: str,
    body: ModuleDocumentsRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    types = await asyncio.to_thread(list_document_types, store)
    document_type = types.get(body.type)
    if document_type is None:
        raise HTTPException(status_code=404, detail=f"Unknown document type '{body.type}'.")
    has_more = False
    if document_type.key is not None:
        rows = [
            (
                document_type.key,
                store.get_document(PROGENITOR_STATE_PREFIX, document_type.key, None),
            )
        ]
    elif body.id is not None:
        key = document_type.document_key(body.id)
        rows = [(key, store.get_document(PROGENITOR_STATE_PREFIX, key, None))]
    else:
        rows = store.list_documents(PROGENITOR_STATE_PREFIX, document_type.key_prefix or "")
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
        "module.documents.loaded",
        {"name": name, "type": body.type, "items": items, "cursor": cursor},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.documents.loaded"])


@router.post(
    "/modules/{name}/data", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def module_data(
    name: str,
    body: ModuleDataRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    key = body.key
    if body.op != "list":
        try:
            key = validate_module_data_key(key or "")
        except ModuleValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    value: Any = None
    items: list[dict[str, Any]] | None = None
    if body.op == "list":
        prefix = module_data_key(name, "")
        items = [
            {"key": item_key.removeprefix(prefix), "value": document.payload.get("value")}
            for item_key, document in store.list_documents(PROGENITOR_STATE_PREFIX, prefix)
            if isinstance(document.payload, dict)
        ]
    else:
        assert key is not None
        store_key = module_data_key(name, key)
        if body.op == "get":
            document = store.get_document(PROGENITOR_STATE_PREFIX, store_key, None)
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
                    status_code=400, detail="Module data value must be JSON serializable."
                ) from exc
            if size > MAX_MODULE_DATA_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Module data value is {size} bytes; the limit is {MAX_MODULE_DATA_BYTES}.",
                )
            with document_write_origin("user"):
                store.save_document(
                    PROGENITOR_STATE_PREFIX,
                    store_key,
                    {"value": body.value, "updated_at": _utc_now()},
                )
            value = body.value
        else:
            with document_write_origin("user"):
                store.delete_document(PROGENITOR_STATE_PREFIX, store_key)
    payload: dict[str, Any] = {"name": name, "op": body.op, "key": key, "value": value}
    if items is not None:
        payload["items"] = items
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.data.loaded", payload, request_id=request_id, client_id=body.client_id
    )
    return http_contracts.acknowledgement_response(request_id, ["module.data.loaded"])


@router.put(
    "/modules/{name}/subscription",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def update_module_subscription(
    name: str,
    body: ModuleSubscriptionRequest,
    request: Request,
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    types = await asyncio.to_thread(list_document_types, store)
    events = [_validate_pattern(pattern, types) for pattern in body.events]
    module_events.set_interest(body.client_id, name, events)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.subscription.updated",
        {"name": name, "events": events},
        request_id=request_id,
        client_id=body.client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.subscription.updated"])


@router.delete(
    "/modules/{name}/subscription",
    status_code=202,
    response_model=http_contracts.UiRequestAcknowledgement,
)
async def delete_module_subscription(
    name: str,
    request: Request,
    client_id: str = Query(min_length=1, max_length=64),
    x_request_id: str | None = _REQUEST_ID_HEADER,
) -> http_contracts.UiRequestAcknowledgement:
    store = get_default_document_store()
    name = await asyncio.to_thread(_require_module, store, name)
    module_events.clear_interest(client_id, name)
    request_id = _request_correlation_id(request, x_request_id)
    events_stream.broadcast_ui_event(
        "module.subscription.updated",
        {"name": name, "events": []},
        request_id=request_id,
        client_id=client_id,
    )
    return http_contracts.acknowledgement_response(request_id, ["module.subscription.updated"])


def broadcast_modules_snapshot(
    *, request_id: str | None = None
) -> events_contracts.UiEventEnvelope:
    """Broadcast the live summary of every agent-built module."""

    from app.backend.modules.routes import module_summaries

    items = [summary.model_dump(mode="json") for summary in module_summaries()]
    return events_stream.broadcast_ui_event(
        "modules.snapshot", {"items": items}, request_id=request_id
    )
