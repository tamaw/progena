"""Storage routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, Request

import src.backend.events.documents as events_documents
import src.backend.events.stream as events_stream
import src.backend.http.contracts as http_contracts
import src.backend.http.requests as http_requests
import src.backend.storage.documents as storage_documents
from src.backend.events import contracts as events_contracts
from src.progena.documents.types import DocumentType
from src.progena.workflows.ports import (
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
    x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
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
        events_contracts.UiEvent.DOCUMENT_TYPES_SNAPSHOT, {"items": items}, request_id=request_id
    )
    return http_contracts.acknowledgement_response(
        request_id, [events_contracts.UiEvent.DOCUMENT_TYPES_SNAPSHOT]
    )
