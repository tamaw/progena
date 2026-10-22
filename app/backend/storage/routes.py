"""Storage routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, Request

import app.backend.events.documents as events_documents
import app.backend.events.stream as events_stream
import app.backend.http.contracts as http_contracts
import app.backend.http.requests as http_requests
import app.backend.storage.documents as storage_documents
from app.progenitor.document_types import DocumentType
from app.progenitor.workflows import (
    document_create_input_schema,
    document_create_output_schema,
    document_delete_input_schema,
    document_delete_output_schema,
    document_event_output_schema,
    document_query_output_schema,
    document_read_input_schema,
    document_read_output_schema,
    document_write_input_schema,
    document_write_output_schema,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _document_type_snapshot_item(document_type: DocumentType) -> dict[str, Any]:
    """Describe one document type with every port schema a workflow editor needs."""

    document_schema = document_type.load_schema()
    return {
        "id": document_type.id,
        "label": document_type.label,
        "key_prefix": document_type.key_prefix,
        "key": document_type.key,
        "collection": document_type.collection,
        "schema": document_type.schema_name,
        "document_schema": document_schema,
        "event_output_schema": document_event_output_schema(document_type, document_schema),
        "read_input_schema": document_read_input_schema(document_type),
        "read_output_schema": document_read_output_schema(document_type, document_schema),
        "query_output_schema": document_query_output_schema(document_type, document_schema),
        "writable_fields": list(document_type.writable_fields),
        "write_input_schema": document_write_input_schema(document_type, document_schema)
        if document_type.writable_fields
        else None,
        "write_output_schema": document_write_output_schema(document_type, document_schema)
        if document_type.writable_fields
        else None,
        "create_input_schema": document_create_input_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
        "create_output_schema": document_create_output_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
        "delete_input_schema": document_delete_input_schema()
        if document_type.collection and document_schema is not None
        else None,
        "delete_output_schema": document_delete_output_schema(document_type, document_schema)
        if document_type.collection and document_schema is not None
        else None,
    }


@router.get(
    "/document-types", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
)
async def get_document_types(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Publish the document type registry (``document_type:<id>`` rows).

    Backs the workflow editor's document type pickers; each item carries the
    exact port schemas the server derives for event/read/query nodes on save.
    """
    request_id = http_requests.request_correlation_id(request, x_request_id)
    types = events_documents.list_document_types(storage_documents.get_default_document_store())
    items = [
        _document_type_snapshot_item(item)
        for item in sorted(types.values(), key=lambda item: item.label.lower())
    ]
    events_stream.broadcast_ui_event(
        "document_types.snapshot", {"items": items}, request_id=request_id
    )
    return http_contracts.acknowledgement_response(request_id, ["document_types.snapshot"])
