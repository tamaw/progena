"""Node input/output contracts for documents, routines and executable nodes."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from src.progena.documents.schema import typed_document_schema
from src.progena.documents.types import DOCUMENT_OPERATIONS, DocumentType
from src.progena.workflows.constants import (
    _ANY_VALUE_SCHEMA,
    _TRIGGER_INPUT_SCHEMA,
    ROUTINE_MESSAGE_INPUT_SCHEMA,
    DocumentQueryOperator,
)
from src.progena.workflows.models import (
    DocumentQueryFilter,
    WorkflowNode,
    WorkflowNodeCron,
    WorkflowNodeData,
    WorkflowNodeEvent,
    WorkflowNodeManual,
)
from src.progena.workflows.schema_values import unpackable_properties


def document_event_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return the output contract of an event node on ``document_type``.

    ``before``/``after`` carry the whole document as one typed port; Unpack
    nodes expose its fields. Sides absent for an operation (``before`` on
    created, ``after`` on deleted) are simply not produced.
    """

    embedded, definitions = typed_document_schema(document_type, document_schema)
    return {
        "title": f"{document_type.label} event",
        "type": "object",
        "required": ["event_id", "document_type", "key", "id", "op", "timestamp", "changed_fields"],
        "properties": {
            "event_id": {"type": "integer"},
            "document_type": {"type": "string", "const": document_type.id},
            "key": {"type": "string"},
            "id": {"type": "string"},
            "op": {"type": "string", "enum": list(DOCUMENT_OPERATIONS)},
            "timestamp": {"type": "string"},
            "changed_fields": {"type": "array", "items": {"type": "string"}},
            "before": copy.deepcopy(embedded),
            "after": embedded,
        },
        **definitions,
    }


def document_read_input_schema(document_type: DocumentType) -> dict[str, Any]:
    """Collections are read by id; a single document only needs a sequencing input."""

    if document_type.collection:
        return {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
    return copy.deepcopy(_TRIGGER_INPUT_SCHEMA)


def document_read_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    embedded, definitions = typed_document_schema(document_type, document_schema)
    return {
        "title": f"{document_type.label} read",
        "type": "object",
        "required": ["found", "key", "id"],
        "properties": {
            "found": {"type": "boolean"},
            "key": {"type": "string"},
            "id": {"type": "string"},
            "document": embedded,
        },
        **definitions,
    }


def document_query_filter_port_names(filters: list[DocumentQueryFilter]) -> list[str]:
    """Return stable input port names, suffixing repeated document fields."""

    counts = {item.field: sum(other.field == item.field for other in filters) for item in filters}
    return [
        item.field if counts[item.field] == 1 else f"{item.field}_{item.op}" for item in filters
    ]


def _query_filter_input_schema(
    field_schema: Mapping[str, Any], op: DocumentQueryOperator
) -> dict[str, Any]:
    if op == "in":
        return {"type": "array", "items": copy.deepcopy(dict(field_schema))}
    if op == "contains":
        raw_item_schema = field_schema.get("items")
        item_schema: Mapping[str, Any] = (
            raw_item_schema if isinstance(raw_item_schema, Mapping) else {}
        )
        return {
            "anyOf": [
                {"type": "string"},
                copy.deepcopy(dict(item_schema)),
            ]
        }
    if op == "exists":
        return {"type": "boolean"}
    return copy.deepcopy(dict(field_schema))


def document_query_input_schema(
    document_type: DocumentType,
    document_schema: Mapping[str, Any] | None,
    filters: list[DocumentQueryFilter],
) -> dict[str, Any]:
    if not filters:
        return copy.deepcopy(_TRIGGER_INPUT_SCHEMA)
    embedded, definitions = typed_document_schema(document_type, document_schema)
    properties = unpackable_properties(embedded)
    unknown = [item.field for item in filters if item.field not in properties]
    if unknown:
        raise ValueError(
            f"document type '{document_type.id}' has no field(s) {', '.join(sorted(set(unknown)))}"
        )
    port_names = document_query_filter_port_names(filters)
    if len(set(port_names)) != len(port_names):
        raise ValueError("document_query filters duplicate the same field and operator")
    return {
        "type": "object",
        "properties": {
            port: _query_filter_input_schema(properties[item.field], item.op)
            for item, port in zip(filters, port_names, strict=True)
        },
        **definitions,
    }


def document_query_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    embedded, definitions = typed_document_schema(document_type, document_schema)
    return {
        "title": f"{document_type.label} query",
        "type": "object",
        "required": ["items", "keys", "count", "total"],
        "properties": {
            "items": {"type": "array", "items": embedded},
            "keys": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer"},
            "total": {"type": "integer"},
        },
        **definitions,
    }


def document_write_input_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Input ports: ``id`` for collections, plus one optional port per writable field."""
    embedded, definitions = typed_document_schema(document_type, document_schema)
    properties: dict[str, Any] = {}
    if document_type.collection:
        properties["id"] = {"type": "string"}
    writable_props = unpackable_properties(embedded)
    for field in document_type.writable_fields:
        properties[field] = copy.deepcopy(writable_props.get(field, _ANY_VALUE_SCHEMA))
    required = ["id"] if document_type.collection else []
    return {"type": "object", "required": required, "properties": properties, **definitions}


def document_write_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Output ports: ``found`` plus one optional port per writable field."""
    embedded, definitions = typed_document_schema(document_type, document_schema)
    writable_props = unpackable_properties(embedded)
    properties: dict[str, Any] = {"found": {"type": "boolean"}}
    for field in document_type.writable_fields:
        properties[field] = copy.deepcopy(writable_props.get(field, _ANY_VALUE_SCHEMA))
    return {
        "title": f"{document_type.label} write",
        "type": "object",
        "required": ["found"],
        "properties": properties,
        **definitions,
    }


def document_create_input_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Input ports for a collection insertion, including optional caller id."""

    embedded, definitions = typed_document_schema(document_type, document_schema)
    writable_props = unpackable_properties(embedded)
    schema_required = (
        document_schema.get("required", []) if isinstance(document_schema, Mapping) else []
    )
    properties: dict[str, Any] = {"id": {"type": "string"}}
    for field in document_type.writable_fields:
        properties[field] = copy.deepcopy(writable_props.get(field, _ANY_VALUE_SCHEMA))
    required = [
        field
        for field in schema_required
        if field in document_type.writable_fields
        and "default" not in writable_props.get(field, _ANY_VALUE_SCHEMA)
    ]
    return {"type": "object", "required": required, "properties": properties, **definitions}


def document_create_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    embedded, definitions = typed_document_schema(document_type, document_schema)
    return {
        "title": f"{document_type.label} create",
        "type": "object",
        "required": ["id", "key", "document"],
        "properties": {
            "id": {"type": "string"},
            "key": {"type": "string"},
            "document": embedded,
        },
        **definitions,
    }


def document_delete_input_schema() -> dict[str, Any]:
    return {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}


def document_delete_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    embedded, definitions = typed_document_schema(document_type, document_schema)
    return {
        "title": f"{document_type.label} delete",
        "type": "object",
        "required": ["found", "before"],
        "properties": {
            "found": {"type": "boolean"},
            "before": {"anyOf": [embedded, {"type": "null"}]},
        },
        **definitions,
    }


def _derive_agent_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["message", "thread_id"],
        "properties": {
            "message": {"type": "string"},
            "thread_id": {"type": "string"},
        },
    }


def routine_declared_schema(schema: Any) -> dict[str, Any] | None:
    """A routine input/output schema, if it declares fields.

    Same rule the routine compiler uses: only ``properties`` names count, so an
    absent or property-less schema means the routine takes and returns a
    conversational message instead.
    """

    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    if isinstance(properties, Mapping) and properties:
        return copy.deepcopy(dict(schema))
    return None


def routine_node_port_schemas(
    routine_id: str, graph: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (input schema, output schema) for a routine node running ``graph``.

    Inputs are the routine's ``input_schema`` fields, else one ``message``
    string. Outputs are its ``output_schema`` fields, else ``message`` (its
    final reply), plus the routine run's ``thread_id``. The graph's ``$defs``
    are hoisted to both roots so ``#/$defs/...`` refs keep resolving.
    """

    raw_defs = graph.get("$defs")
    definitions = (
        {"$defs": copy.deepcopy(dict(raw_defs))}
        if isinstance(raw_defs, Mapping) and raw_defs
        else {}
    )
    declared_input = routine_declared_schema(graph.get("input_schema"))
    input_schema = (
        declared_input | definitions
        if declared_input is not None
        else copy.deepcopy(ROUTINE_MESSAGE_INPUT_SCHEMA)
    )
    declared_output = routine_declared_schema(graph.get("output_schema"))
    if declared_output is None:
        return input_schema, _derive_agent_output_schema()
    properties = dict(declared_output["properties"])
    if "thread_id" in properties:
        raise ValueError(
            f"routine '{routine_id}' output_schema field 'thread_id' is reserved for the "
            "routine run's thread"
        )
    properties["thread_id"] = {"type": "string"}
    output_schema = {
        **declared_output,
        "properties": properties,
        "required": [*declared_output.get("required", []), "thread_id"],
    } | definitions
    return input_schema, output_schema


def workflow_node_output_ports(node: WorkflowNode) -> dict[str, dict[str, Any]]:
    """Return the named, typed output ports for one node."""

    if isinstance(node, WorkflowNodeData):
        return {"value": dict(node.output_schema)}
    schema = node.output_schema or {}
    if schema.get("type") != "object":
        return {}
    return dict(schema.get("properties") or {})


def workflow_node_input_ports(node: WorkflowNode) -> dict[str, dict[str, Any]]:
    """Return the named, typed input ports for one node."""

    if isinstance(
        node, WorkflowNodeData | WorkflowNodeEvent | WorkflowNodeCron | WorkflowNodeManual
    ):
        return {}
    schema = node.input_schema or {}
    if schema.get("type") != "object":
        return {}
    return dict(schema.get("properties") or {})


def workflow_node_output_object(node: WorkflowNode) -> dict[str, Any] | None:
    """Return the immediately available output object for literal data nodes."""

    if isinstance(node, WorkflowNodeData):
        return {"value": node.value}
    return None
