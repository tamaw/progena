"""Shared workflow definitions, validation, and helpers."""

from __future__ import annotations

import copy
import json
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .cron import cron_error, resolve_timezone
from .document_kinds import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from .document_types import DOCUMENT_OPERATIONS, DocumentOperation, DocumentType
from .instructions.templates import (
    parse_format_template,
    parse_instruction_template,
    resolve_type_ref,
)
from .schemas import load_schema
from .structured import validate_against_schema
from .utils import utcnow_isoformat

WORKFLOW_DEFAULT_DOCUMENT_TYPE = "kanban_card"

WORKFLOW_VERSION = 3
# Max chained Progenitor-initiated workflow invocations.
MAX_WORKFLOW_INVOKE_DEPTH = 3
# Upper bound on documents one document_query node may return.
MAX_WORKFLOW_QUERY_LIMIT = 200

WorkflowRunStatus = Literal["queued", "running", "paused", "completed", "failed"]
WorkflowRunSource = Literal["document", "manual", "progenitor", "cron"]
WorkflowCronOverlap = Literal["skip", "queue"]

_JSON_OBJECT_SCHEMA = {"type": "object"}
_ANY_VALUE_SCHEMA: dict[str, Any] = {}
_TRIGGER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["trigger"],
    "properties": {"trigger": _ANY_VALUE_SCHEMA},
}

# Root keywords that only make sense at a schema's own root. ``$id`` would
# re-base ``$ref`` resolution, so it is dropped; definitions are hoisted to the
# composed port schema's root so internal ``#/$defs/...`` refs keep resolving.
_SCHEMA_ROOT_ONLY_KEYS = ("$schema", "$id")
_SCHEMA_DEFINITION_KEYS = ("$defs", "definitions")


def _typed_document_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return (embeddable document schema titled with the type label, root definitions).

    The title makes the port nominally typed: two document types sharing one
    schema file still only connect to ports of their own type.
    """

    embedded: dict[str, Any] = (
        copy.deepcopy(dict(document_schema)) if document_schema else dict(_JSON_OBJECT_SCHEMA)
    )
    for key in _SCHEMA_ROOT_ONLY_KEYS:
        embedded.pop(key, None)
    definitions = {
        key: embedded.pop(key)
        for key in _SCHEMA_DEFINITION_KEYS
        if isinstance(embedded.get(key), dict)
    }
    embedded["title"] = document_type.label
    return embedded, definitions


def document_event_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return the output contract of an event node on ``document_type``.

    ``before``/``after`` carry the whole document as one typed port; Unpack
    nodes expose its fields. Sides absent for an operation (``before`` on
    created, ``after`` on deleted) are simply not produced.
    """

    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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


DocumentQueryOperator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "exists"]


class DocumentQueryFilter(BaseModel):
    """One top-level document predicate and its derived input-port name."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    op: DocumentQueryOperator = "eq"


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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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

    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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
    embedded, definitions = _typed_document_schema(document_type, document_schema)
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


def json_equal(a: Any, b: Any) -> bool:
    """Equality as JSONB sees it: ``true`` is not ``1``, but ``1`` is ``1.0``."""

    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(json_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict):
        return set(a) == set(b) and all(json_equal(a[k], b[k]) for k in a)
    return a == b


def document_changed_fields(before: Any, after: Any) -> list[str]:
    """Top-level fields whose value differs between two document payloads."""

    previous = before if isinstance(before, Mapping) else {}
    current = after if isinstance(after, Mapping) else {}
    return sorted(
        field
        for field in set(previous) | set(current)
        if field not in previous
        or field not in current
        or not json_equal(previous[field], current[field])
    )


def build_document_event_output(
    *,
    event_id: int,
    document_type: DocumentType,
    key: str,
    op: DocumentOperation,
    before: Any,
    after: Any,
    timestamp: str,
) -> dict[str, Any]:
    """Build the output object an event node emits for one document event."""

    output: dict[str, Any] = {
        "event_id": event_id,
        "document_type": document_type.id,
        "key": key,
        "id": document_type.document_id(key),
        "op": op,
        "timestamp": timestamp,
        "changed_fields": document_changed_fields(before, after),
    }
    if before is not None:
        output["before"] = before
    if after is not None:
        output["after"] = after
    return output


def build_document_read_output(
    document_type: DocumentType, key: str, document: Any | None
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "found": document is not None,
        "key": key,
        "id": document_type.document_id(key),
    }
    if document is not None:
        output["document"] = document
    return output


def value_at_path(value: Any, path: str) -> Any:
    """Walk a dotted path (``after.column_id``) into nested objects; ``None`` if absent."""

    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def path_exists(value: Any, path: str) -> bool:
    """Whether every segment of a dotted path is present."""

    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return False
        value = value[part]
    return True


def schema_at_path(ports: Mapping[str, Any], path: str) -> dict[str, Any] | None:
    """Resolve a dotted path against port schemas' nested ``properties``.

    ``None`` when a segment is not declared; an object schema that declares no
    ``properties`` at all is open, so any deeper path resolves to ``{}``.
    """

    schema: Any = {"properties": ports}
    for part in path.split("."):
        if not isinstance(schema, Mapping):
            return None
        properties = schema.get("properties")
        if properties is None and _admits_object(schema):
            return {}
        if not isinstance(properties, Mapping) or part not in properties:
            return None
        schema = properties[part]
    return dict(schema) if isinstance(schema, Mapping) else {}


def _admits_object(schema: Mapping[str, Any]) -> bool:
    """Whether the schema's ``type`` is ``object`` or a union including it."""

    schema_type = schema.get("type")
    return schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)


def unpackable_properties(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Properties of an object port schema an Unpack node can expose (empty if none).

    A nullable object (``["object", "null"]``) exposes its fields too.
    """

    if not isinstance(schema, Mapping) or not _admits_object(schema):
        return {}
    properties = schema.get("properties")
    return dict(properties) if isinstance(properties, Mapping) else {}


def _resolve_local_ref(
    schema: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Follow ``#/$defs/...`` / ``#/definitions/...`` references to their target schema."""

    seen: set[str] = set()
    while isinstance(schema, Mapping) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        container, _, name = ref.removeprefix("#/").partition("/")
        group = definitions.get(container) if ref.startswith("#/") else None
        target = group.get(name) if isinstance(group, Mapping) and "/" not in name else None
        if ref in seen or not isinstance(target, Mapping):
            return schema
        seen.add(ref)
        schema = target
    return schema


class WorkflowNodeEvent(BaseModel):
    """Entrypoint fired by create/update/delete events on one document type.

    Filters are all optional: ``key`` pins one document, ``operations`` limits
    the op (empty = any), ``changed_fields`` requires at least one listed
    top-level field to differ, and ``match`` requires equality at dotted
    output paths (e.g. ``{"after.column_id": "done"}``).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event"] = "event"
    title: str = "Document event"
    document_type: str = Field(min_length=1)
    key: str | None = None
    operations: list[DocumentOperation] = Field(default_factory=list)
    changed_fields: list[str] = Field(default_factory=list)
    match: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))

    def matches(self, event: Mapping[str, Any]) -> bool:
        """Whether one document event output object satisfies every filter."""

        if event.get("document_type") != self.document_type:
            return False
        if self.key and event.get("key") != self.key:
            return False
        if self.operations and event.get("op") not in self.operations:
            return False
        if self.changed_fields and not set(self.changed_fields) & set(
            event.get("changed_fields") or []
        ):
            return False
        return all(
            json_equal(value_at_path(event, path), value) and path_exists(event, path)
            for path, value in self.match.items()
        )


CRON_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scheduled_for", "fired_at", "timezone"],
    "properties": {
        "scheduled_for": {"type": "string", "format": "date-time"},
        "fired_at": {"type": "string", "format": "date-time"},
        "timezone": {"type": "string"},
    },
}


class WorkflowNodeCron(BaseModel):
    """Entrypoint fired by the backend clock on a 5-field cron expression.

    ``overlap`` decides what a fire does while the workflow still has an
    active run: ``skip`` drops it, ``queue`` runs it after the active one.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["cron"] = "cron"
    title: str = "Cron"
    expression: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    overlap: WorkflowCronOverlap = "skip"
    output_schema: dict[str, Any] = Field(default_factory=lambda: copy.deepcopy(CRON_OUTPUT_SCHEMA))


WorkflowTriggerNode = WorkflowNodeEvent | WorkflowNodeCron


def build_cron_trigger_output(
    node: WorkflowNodeCron, *, scheduled_for: datetime, fired_at: datetime
) -> dict[str, Any]:
    """The output object a cron node emits for one due time."""

    zone = resolve_timezone(node.timezone)
    return {
        "scheduled_for": scheduled_for.astimezone(zone).isoformat(),
        "fired_at": fired_at.astimezone(zone).isoformat(),
        "timezone": node.timezone,
    }


class WorkflowNodeData(BaseModel):
    """A literal typed value node with no execution behavior."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["data"] = "data"
    title: str = "Data"
    value: Any = None
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentRead(BaseModel):
    """Deterministic read of one document of a type; no LLM involved.

    Schemas are derived from the document type on save (see
    ``derive_workflow_port_schemas``).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_read"] = "document_read"
    title: str = "Read document"
    document_type: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentQuery(BaseModel):
    """Deterministic filtered query over a collection document type."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_query"] = "document_query"
    title: str = "Query documents"
    document_type: str = Field(min_length=1)
    filters: list[DocumentQueryFilter] = Field(default_factory=list)
    order_by: str | None = None
    order: Literal["asc", "desc"] = "asc"
    limit: int = Field(default=50, ge=1, le=MAX_WORKFLOW_QUERY_LIMIT)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentWrite(BaseModel):
    """Atomic partial write of one document; schemas decide writable fields."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_write"] = "document_write"
    title: str = "Write document"
    document_type: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentCreate(BaseModel):
    """Atomic insertion of one document into a collection."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_create"] = "document_create"
    title: str = "Create document"
    document_type: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentDelete(BaseModel):
    """Atomic deletion of one document from a collection."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_delete"] = "document_delete"
    title: str = "Delete document"
    document_type: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


RepoNodeAction = Literal["sync", "worktree", "publish", "remove_worktree"]


def repo_node_port_schemas(action: RepoNodeAction) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the fixed input/output contracts for one repo-management action."""
    required_input: dict[RepoNodeAction, list[str]] = {
        "sync": ["repo_id"],
        "worktree": ["repo_id", "branch"],
        "publish": ["repo_id", "branch", "title"],
        "remove_worktree": ["repo_id", "branch"],
    }
    input_properties: dict[RepoNodeAction, dict[str, Any]] = {
        "sync": {"repo_id": {"type": "string"}},
        "worktree": {
            "repo_id": {"type": "string"},
            "branch": {"type": "string"},
            "base": {"type": "string"},
        },
        "publish": {
            "repo_id": {"type": "string"},
            "branch": {"type": "string"},
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "remove_worktree": {"repo_id": {"type": "string"}, "branch": {"type": "string"}},
    }
    output_properties: dict[RepoNodeAction, dict[str, Any]] = {
        "sync": {
            "repo_id": {"type": "string"},
            "path": {"type": "string"},
            "default_branch": {"type": "string"},
            "head": {"type": "string"},
        },
        "worktree": {
            "repo_id": {"type": "string"},
            "branch": {"type": "string"},
            "path": {"type": "string"},
            "head": {"type": "string"},
            "created": {"type": "boolean"},
        },
        "publish": {
            "repo_id": {"type": "string"},
            "branch": {"type": "string"},
            "pushed": {"type": "boolean"},
            "pr_url": {"type": "string"},
            "pr_number": {"type": "integer"},
        },
        "remove_worktree": {
            "repo_id": {"type": "string"},
            "branch": {"type": "string"},
            "removed": {"type": "boolean"},
        },
    }
    return (
        {
            "type": "object",
            "required": required_input[action],
            "properties": input_properties[action],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "required": list(output_properties[action]),
            "properties": output_properties[action],
            "additionalProperties": False,
        },
    )


class WorkflowNodeRepo(BaseModel):
    """Synchronous managed-repository operation executed by the backend."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["repo"] = "repo"
    title: str = "Repo"
    action: RepoNodeAction
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeUnpack(BaseModel):
    """Pure step exposing one object's properties as output ports.

    Its single ``value`` input takes any object port; output ports are that
    object's properties, re-derived from the upstream port on every save (see
    ``derive_workflow_port_schemas``). Which ports the editor lists is view
    state kept in the definition's ``ui`` section.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unpack"] = "unpack"
    title: str = "Unpack"
    input_schema: dict[str, Any] = Field(default_factory=lambda: _unpack_input_schema(None, {}))
    output_schema: dict[str, Any] = Field(default_factory=lambda: _unpack_output_schema(None, {}))


def _unpack_input_schema(
    source: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["value"],
        "properties": {"value": copy.deepcopy(dict(source)) if source else {"type": "object"}},
        **copy.deepcopy(dict(definitions)),
    }


def _unpack_output_schema(
    source: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> dict[str, Any]:
    source = _resolve_local_ref(source, definitions)
    required = source.get("required") if source else None
    return {
        "type": "object",
        "properties": copy.deepcopy(unpackable_properties(source)),
        "required": list(required) if isinstance(required, list) else [],
        **copy.deepcopy(dict(definitions)),
    }


class WorkflowNodeFormat(BaseModel):
    """Deterministic plain-text template rendering step."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["format"] = "format"
    title: str = "Format"
    template: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeExtract(BaseModel):
    """Prompt-backed extraction step with structured output."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["extract"] = "extract"
    title: str = "Extract"
    instruction: str | None = None
    template: str | None = None
    schema_ref: str | dict | None = Field(default=None, alias="schema")
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeAgent(BaseModel):
    """Prompt-backed agent step delegating to Progenitor."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agent"] = "agent"
    title: str = "Agent"
    instruction: str | None = None
    template: str | None = None
    subagent: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeRoutine(BaseModel):
    """Deterministic run of one routine; no LLM decides whether it runs.

    Ports are derived from the routine graph on save (see
    ``routine_node_port_schemas``). A routine that pauses at a human node
    pauses the workflow run until the routine is resumed.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["routine"] = "routine"
    title: str = "Routine"
    routine: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeCondition(BaseModel):
    """Branch node that emits the selected branch as a typed value."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["condition"] = "condition"
    title: str = "Condition"
    expression: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"true": {"type": "boolean"}, "false": {"type": "boolean"}},
        }
    )


class WorkflowNodeApproval(BaseModel):
    """Approval gate that emits the selected approval result as a value."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval"] = "approval"
    title: str = "Approval"
    message: str
    # "human": only a person may decide (the UI); "any": Progenitor may too.
    approver: Literal["human", "any"] = "any"
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "approved": {"type": "boolean"},
                "rejected": {"type": "boolean"},
            },
        }
    )


WorkflowNode = Annotated[
    WorkflowNodeEvent
    | WorkflowNodeCron
    | WorkflowNodeData
    | WorkflowNodeDocumentRead
    | WorkflowNodeDocumentQuery
    | WorkflowNodeDocumentWrite
    | WorkflowNodeRepo
    | WorkflowNodeDocumentCreate
    | WorkflowNodeDocumentDelete
    | WorkflowNodeFormat
    | WorkflowNodeUnpack
    | WorkflowNodeExtract
    | WorkflowNodeAgent
    | WorkflowNodeRoutine
    | WorkflowNodeCondition
    | WorkflowNodeApproval,
    Field(discriminator="kind"),
]


class WorkflowEdgeEndpoint(BaseModel):
    """One end of a workflow edge."""

    model_config = ConfigDict(extra="forbid")

    node: str
    port: str = "default"


class WorkflowEdge(BaseModel):
    """A directed typed value connection that also establishes dependency."""

    model_config = ConfigDict(extra="forbid")

    from_: WorkflowEdgeEndpoint = Field(alias="from")
    to: WorkflowEdgeEndpoint


class WorkflowGraph(BaseModel):
    """Executable workflow graph."""

    model_config = ConfigDict(extra="forbid")

    nodes: dict[str, WorkflowNode] = Field(default_factory=dict)
    edges: list[WorkflowEdge] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """Canonical persisted workflow definition."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    enabled: bool = True
    paused: bool = False
    # Raise an alert whenever a run pauses for an approval or a routine's human step.
    notify_on_pause: bool = False
    version: int = WORKFLOW_VERSION
    graph: WorkflowGraph
    ui: dict[str, Any] = Field(default_factory=dict)
    updated_at: str
    created_at: str


class WorkflowListItem(BaseModel):
    """Summary payload for workflow lists."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str = ""
    enabled: bool = True
    paused: bool = False
    version: int = WORKFLOW_VERSION
    document_type: str
    triggers: list[str] = Field(default_factory=list)
    node_count: int = 0
    updated_at: str
    created_at: str


class WorkflowRun(BaseModel):
    """Persisted workflow run state."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_id: str
    status: WorkflowRunStatus
    current_node_id: str | None = None
    completed_node_ids: list[str] = Field(default_factory=list)
    waiting_node_id: str | None = None
    pending_approval: dict[str, Any] | None = None
    # The routine run a routine node is waiting on: node_id, routine_id,
    # session_id (matches the routine's lifecycle events), request_id, the
    # routine thread_id once known, and status (running|paused).
    pending_routine: dict[str, Any] | None = None
    thread_id: str | None = None
    trigger_event: dict[str, Any] = Field(default_factory=dict)
    trigger_node_id: str | None = None
    source: WorkflowRunSource = "manual"
    ancestry: list[str] = Field(default_factory=list)
    # Outputs produced so far, keyed by node id. Data edges resolve strictly
    # from this registry; there is no implicit "previous output" dataflow.
    node_outputs: dict[str, Any] = Field(default_factory=dict)
    last_output: str | None = None
    last_error: str | None = None
    step_audit: list[dict[str, Any]] = Field(default_factory=list)
    queued_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None


def workflow_definition_key(workflow_id: str) -> str:
    """Return the runtime document key for one workflow definition."""

    return f"{WORKFLOW_DEFINITION_KEY_PREFIX}{workflow_id}"


def workflow_run_key(run_id: str) -> str:
    """Return the runtime document key for one workflow run."""
    return f"{WORKFLOW_RUN_KEY_PREFIX}{run_id}"


def workflow_queue_key(workflow_id: str) -> str:
    """Return the runtime document key for one workflow trigger queue."""
    return f"{WORKFLOW_QUEUE_KEY_PREFIX}{workflow_id}"


def workflow_invoke_request_key(request_id: str) -> str:
    """Return the runtime document key for one workflow invoke request."""

    return f"{WORKFLOW_INVOKE_REQUEST_KEY_PREFIX}{request_id}"


def workflow_file_name(workflow_id: str) -> str:
    """Return the mounted virtual filename for one workflow definition."""

    return f"{workflow_id}.json"


def workflow_id_from_path(path_or_id: str) -> str:
    """Normalize a workflow id from ``<id>``, ``<id>.json``, or ``/config/...``."""

    normalized = str(path_or_id or "").strip()
    if not normalized:
        raise ValueError("workflow id is required")
    if normalized.startswith("/"):
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized.endswith(".json"):
        normalized = normalized[: -len(".json")]
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("workflow id is required")
    return normalized


def build_workflow_list_item(definition: WorkflowDefinition) -> WorkflowListItem:
    """Project a stored workflow definition into a list-card payload."""

    event_node = next(
        (node for node in definition.graph.nodes.values() if isinstance(node, WorkflowNodeEvent)),
        None,
    )
    triggers = [
        node.document_type if isinstance(node, WorkflowNodeEvent) else f"cron {node.expression}"
        for node in definition.graph.nodes.values()
        if isinstance(node, WorkflowTriggerNode)
    ]
    return WorkflowListItem(
        id=definition.id,
        title=definition.title,
        description=definition.description,
        enabled=definition.enabled,
        paused=definition.paused,
        version=definition.version,
        document_type=event_node.document_type if event_node else "",
        triggers=triggers,
        node_count=len(definition.graph.nodes),
        updated_at=definition.updated_at,
        created_at=definition.created_at,
    )


def default_workflow_definition(
    *,
    workflow_id: str | None = None,
    title: str = "Untitled workflow",
    description: str = "",
    document_type: str = WORKFLOW_DEFAULT_DOCUMENT_TYPE,
) -> dict[str, Any]:
    """Return the minimal graph-native workflow document.

    Event port schemas are filled in by ``derive_workflow_port_schemas`` on save.
    """

    created_at = utcnow_isoformat()
    resolved_id = workflow_id or f"workflow_{uuid.uuid4().hex[:10]}"
    event_node_id = f"event_{uuid.uuid4().hex[:8]}"
    agent_node_id = f"agent_{uuid.uuid4().hex[:8]}"
    return {
        "id": resolved_id,
        "title": title,
        "description": description,
        "enabled": True,
        "paused": False,
        "version": WORKFLOW_VERSION,
        "graph": {
            "nodes": {
                event_node_id: {
                    "kind": "event",
                    "title": "Document event",
                    "document_type": document_type,
                    "key": None,
                    "operations": [],
                    "changed_fields": [],
                    "match": {},
                    "output_schema": dict(_JSON_OBJECT_SCHEMA),
                },
                agent_node_id: {
                    "kind": "agent",
                    "title": "Agent",
                    "template": "Handle this workflow event.\n\nDocument key: {key}",
                    "input_schema": {
                        "type": "object",
                        "required": ["key"],
                        "properties": {"key": {"type": "string"}},
                    },
                    "output_schema": {
                        "type": "object",
                        "required": ["message", "thread_id"],
                        "properties": {
                            "message": {"type": "string"},
                            "thread_id": {"type": "string"},
                        },
                    },
                },
            },
            "edges": [
                {
                    "from": {"node": event_node_id, "port": "key"},
                    "to": {"node": agent_node_id, "port": "key"},
                }
            ],
        },
        "ui": {
            "nodes": {
                event_node_id: {
                    "position": {"x": 96, "y": 160},
                    "size": {
                        "data": {"width": 320, "height": 220},
                        "settings": {"width": 340, "height": 260},
                    },
                },
                agent_node_id: {
                    "position": {"x": 456, "y": 160},
                    "size": {
                        "data": {"width": 340, "height": 160},
                        "settings": {"width": 340, "height": 280},
                    },
                },
            },
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        },
        "updated_at": created_at,
        "created_at": created_at,
    }


def _node_instruction_text(
    node: dict[str, Any], instruction_loader: Callable[[str], str] | None
) -> str:
    """Return the instruction template text for an extract/agent node."""
    instruction_path = node.get("instruction")
    if instruction_path:
        if instruction_loader is None:
            raise ValueError(
                f"node '{node.get('title')}' references instruction '{instruction_path}' but "
                f"no instruction loader is available"
            )
        return instruction_loader(instruction_path)
    template = node.get("template")
    if isinstance(template, str):
        return template
    raise ValueError("extract/agent node must have either 'instruction' or 'template'")


def _derive_prompt_node_input_schema(
    node: dict[str, Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None,
) -> dict[str, Any]:
    """Derive input schema for an extract/agent node from its prompt template."""
    text = _node_instruction_text(node, instruction_loader)
    template = parse_instruction_template(text)
    properties: dict[str, Any] = {}
    definitions: dict[str, dict[str, Any]] = {}
    for root in template.roots:
        type_ref = template.inputs.get(root)
        if type_ref is not None:
            properties[root], root_definitions = resolve_type_ref(type_ref, document_types)
            _merge_definitions(definitions, root_definitions)
        else:
            properties[root] = _ANY_VALUE_SCHEMA
    properties["thread_id"] = {"type": "string"}
    required = list(template.roots)
    return {"type": "object", "required": required, "properties": properties, **definitions}


def _derive_format_node_input_schema(node: dict[str, Any]) -> dict[str, Any]:
    """Derive one required untyped input port per format placeholder root."""

    template = parse_format_template(str(node.get("template") or ""))
    roots: list[str] = []
    for placeholder in template.placeholders:
        root = placeholder.split(".", 1)[0]
        if root not in roots:
            roots.append(root)
    return {
        "type": "object",
        "required": roots,
        "properties": {root: {} for root in roots},
    }


def _derive_format_node_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }


def _merge_definitions(
    target: dict[str, dict[str, Any]], source: Mapping[str, Mapping[str, Any]]
) -> None:
    """Merge root definition maps; one name with two different schemas is an error."""

    for key, defs in source.items():
        bucket = target.setdefault(key, {})
        for name, schema in defs.items():
            if name in bucket and bucket[name] != schema:
                raise ValueError(f"conflicting schema definitions for '{key}/{name}'")
            bucket[name] = schema


def _extract_result_schema(
    result_schema: dict[str, Any], definitions: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["result", "thread_id"],
        "properties": {
            "result": result_schema,
            "thread_id": {"type": "string"},
        },
        **definitions,
    }


def _derive_extract_output_schema(
    node: dict[str, Any],
    node_id: str,
    nodes: dict[str, Any],
    edges: list[Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None,
) -> dict[str, Any]:
    """Derive the result schema for an extract node with precedence rules."""
    # 1. Node-level schema wins
    node_schema = node.get("schema")
    if node_schema is not None:
        return _extract_result_schema(*resolve_type_ref(node_schema, document_types))

    # 2. Template front matter output_schema
    text = _node_instruction_text(node, instruction_loader)
    template = parse_instruction_template(text)
    if template.output_schema is not None:
        return _extract_result_schema(*resolve_type_ref(template.output_schema, document_types))

    # 3. Infer from downstream non-unpack targets
    target_schemas: list[dict[str, Any]] = []
    target_definitions: dict[str, dict[str, Any]] = {}
    for edge in edges:
        source = edge.get("from") if isinstance(edge, dict) else None
        target = edge.get("to") if isinstance(edge, dict) else None
        if (
            isinstance(source, dict)
            and isinstance(target, dict)
            and source.get("node") == node_id
            and source.get("port") == "result"
        ):
            target_node = nodes.get(str(target.get("node")))
            if not isinstance(target_node, dict) or target_node.get("kind") == "unpack":
                continue
            target_input = (
                target_node.get("input_schema")
                if isinstance(target_node, dict)
                else target_node.input_schema
            )
            target_schema = schema_at_path(
                target_input.get("properties", {}) if isinstance(target_input, Mapping) else {},
                str(target.get("port")),
            )
            # An untyped input ({}: e.g. an Agent placeholder) carries no shape
            # to ask Progenitor for, so it never counts as an inferred schema.
            if target_schema:
                target_schemas.append(target_schema)
                for key in _SCHEMA_DEFINITION_KEYS:
                    defs = target_input.get(key) if isinstance(target_input, Mapping) else None
                    if isinstance(defs, dict):
                        _merge_definitions(target_definitions, {key: defs})

    if not target_schemas:
        raise ValueError(f"extract node '{node_id}' needs a schema")

    first = target_schemas[0]
    for other in target_schemas[1:]:
        if other != first:
            raise ValueError(
                f"extract node '{node_id}' result is wired to multiple targets with "
                f"different schemas"
            )

    return {
        "type": "object",
        "required": ["result", "thread_id"],
        "properties": {
            "result": first,
            "thread_id": {"type": "string"},
        },
        **target_definitions,
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


# Looks up one routine's graph document by id; None when no such routine exists.
RoutineLoader = Callable[[str], Mapping[str, Any] | None]

ROUTINE_MESSAGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["message"],
    "properties": {"message": {"type": "string"}},
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


def derive_workflow_port_schemas(
    payload: Mapping[str, Any],
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None = None,
    routine_loader: RoutineLoader | None = None,
) -> dict[str, Any]:
    """Return ``payload`` with every derived node's port schemas recomputed.

    Cron nodes get the fixed cron output schema. Document nodes take their
    schemas from the registered type schema; Unpack nodes take theirs from the
    port feeding their ``value``
    input, so Unpack chains resolve in dependency order. Extract/agent nodes
    take theirs from their prompt template placeholders, routine nodes from
    the routine graph. Run on every save path so ports never reflect whatever
    schemas the writer sent.
    """

    result = copy.deepcopy(dict(payload))
    graph = result.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, dict):
        return result
    schemas: dict[str, dict[str, Any] | None] = {}
    for node_id, node in nodes.items():
        if isinstance(node, dict) and node.get("kind") == "repo":
            action = node.get("action")
            if action not in {"sync", "worktree", "publish", "remove_worktree"}:
                raise ValueError(f"repo node '{node_id}' has unknown action '{action}'")
            node.setdefault("title", "Repo")
            node["input_schema"], node["output_schema"] = repo_node_port_schemas(action)
            continue
        if isinstance(node, dict) and node.get("kind") == "cron":
            node["output_schema"] = copy.deepcopy(CRON_OUTPUT_SCHEMA)
            continue
        if not isinstance(node, dict) or node.get("kind") not in {
            "event",
            "document_read",
            "document_query",
            "document_write",
            "document_create",
            "document_delete",
        }:
            continue
        type_id = node.get("document_type")
        document_type = document_types.get(type_id) if isinstance(type_id, str) else None
        if document_type is None:
            known = ", ".join(sorted(document_types)) or "none registered"
            raise ValueError(
                f"node '{node_id}' references unknown document type '{type_id}' (known: {known})"
            )
        if document_type.id not in schemas:
            schemas[document_type.id] = document_type.load_schema()
        document_schema = schemas[document_type.id]
        kind = node["kind"]
        if kind == "event":
            node["output_schema"] = document_event_output_schema(document_type, document_schema)
        elif kind == "document_read":
            node["input_schema"] = document_read_input_schema(document_type)
            node["output_schema"] = document_read_output_schema(document_type, document_schema)
        elif kind == "document_write":
            if not document_type.writable_fields:
                raise ValueError(
                    f"document_write node '{node_id}' cannot use '{document_type.id}': "
                    f"it has no writable fields"
                )
            node["input_schema"] = document_write_input_schema(document_type, document_schema)
            node["output_schema"] = document_write_output_schema(document_type, document_schema)
        elif kind == "document_create":
            if not document_type.collection:
                raise ValueError(
                    f"document_create node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            if document_schema is None:
                raise ValueError(
                    f"document_create node '{node_id}' needs document type '{document_type.id}' "
                    "to have a schema"
                )
            node["input_schema"] = document_create_input_schema(document_type, document_schema)
            node["output_schema"] = document_create_output_schema(document_type, document_schema)
        elif kind == "document_delete":
            if not document_type.collection:
                raise ValueError(
                    f"document_delete node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            node["input_schema"] = document_delete_input_schema()
            node["output_schema"] = document_delete_output_schema(document_type, document_schema)
        else:
            if not document_type.collection:
                raise ValueError(
                    f"document_query node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            raw_filters = node.get("filters", [])
            if not isinstance(raw_filters, list):
                raise ValueError(f"document_query node '{node_id}' filters must be a list")
            try:
                filters = [DocumentQueryFilter.model_validate(item) for item in raw_filters]
            except Exception as exc:
                raise ValueError(f"document_query node '{node_id}' has an invalid filter") from exc
            order_by = node.get("order_by")
            if order_by is not None and (
                not isinstance(order_by, str)
                or order_by
                not in unpackable_properties(
                    _typed_document_schema(document_type, document_schema)[0]
                )
            ):
                raise ValueError(
                    f"document_query node '{node_id}' has no order_by field '{order_by}'"
                )
            if node.get("order", "asc") not in {"asc", "desc"}:
                raise ValueError(f"document_query node '{node_id}' order must be 'asc' or 'desc'")
            node["input_schema"] = document_query_input_schema(
                document_type, document_schema, filters
            )
            node["output_schema"] = document_query_output_schema(document_type, document_schema)
    # Before prompt nodes: an Extract may infer its result from a routine input.
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("kind") != "routine":
            continue
        routine_id = node.get("routine")
        routine_graph = (
            routine_loader(routine_id)
            if routine_loader is not None and isinstance(routine_id, str)
            else None
        )
        if not isinstance(routine_id, str) or routine_graph is None:
            raise ValueError(f"routine node '{node_id}' references unknown routine '{routine_id}'")
        node["input_schema"], node["output_schema"] = routine_node_port_schemas(
            routine_id, routine_graph
        )
    raw_edges = graph.get("edges") if isinstance(graph, dict) else None
    edges: list[Any] = raw_edges if isinstance(raw_edges, list) else []
    format_nodes = {
        node_id: node
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") == "format"
    }
    for node in format_nodes.values():
        node["input_schema"] = _derive_format_node_input_schema(node)
        node["output_schema"] = _derive_format_node_output_schema()
    prompt_nodes = {
        node_id: node
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") in {"extract", "agent"}
    }
    # Order matters: every prompt node's inputs first (Extract infers its result
    # from the inputs it feeds), then Extract results, then Unpack (which may
    # unpack an Extract result; an Unpack value never constrains an Extract).
    for node in prompt_nodes.values():
        node["input_schema"] = _derive_prompt_node_input_schema(
            node, document_types, instruction_loader
        )
    for node_id, node in prompt_nodes.items():
        if node["kind"] == "extract":
            node["output_schema"] = _derive_extract_output_schema(
                node, node_id, nodes, edges, document_types, instruction_loader
            )
        else:
            node["output_schema"] = _derive_agent_output_schema()
    _derive_unpack_schemas(nodes, edges)

    return result


def _raw_output_port(node: Any, port: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (port schema, root definitions) for one output port of a raw node dict."""

    schema = node.get("output_schema") if isinstance(node, dict) else None
    if not isinstance(schema, dict):
        return None, {}
    definitions = {
        key: schema[key] for key in _SCHEMA_DEFINITION_KEYS if isinstance(schema.get(key), dict)
    }
    if node.get("kind") == "data":
        return (schema if port == "value" else None), definitions
    properties = schema.get("properties") if schema.get("type") == "object" else None
    port_schema = properties.get(port) if isinstance(properties, dict) else None
    return (port_schema if isinstance(port_schema, dict) else None), definitions


def _derive_unpack_schemas(nodes: dict[str, Any], edges: Any) -> None:
    unpack_ids = [
        node_id
        for node_id, node in nodes.items()
        if isinstance(node, dict) and node.get("kind") == "unpack"
    ]
    if not unpack_ids:
        return
    feeds: dict[str, tuple[str, str]] = {}
    for edge in edges if isinstance(edges, list) else []:
        source = edge.get("from") if isinstance(edge, dict) else None
        target = edge.get("to") if isinstance(edge, dict) else None
        if (
            isinstance(source, dict)
            and isinstance(target, dict)
            and target.get("node") in unpack_ids
            and target.get("port") == "value"
        ):
            feeds[str(target["node"])] = (str(source.get("node")), str(source.get("port")))
    # Each pass settles at least one more link of any Unpack chain; cycles are
    # rejected later by graph validation, so the pass count is bounded.
    for _ in range(len(unpack_ids) + 1):
        changed = False
        for node_id in unpack_ids:
            node = nodes[node_id]
            source_node_id, source_port = feeds.get(node_id, ("", ""))
            source, definitions = _raw_output_port(nodes.get(source_node_id), source_port)
            input_schema = _unpack_input_schema(source, definitions)
            output_schema = _unpack_output_schema(source, definitions)
            if (
                node.get("input_schema") != input_schema
                or node.get("output_schema") != output_schema
            ):
                node["input_schema"] = input_schema
                node["output_schema"] = output_schema
                changed = True
        if not changed:
            break


def validate_workflow_definition_document(
    content: str,
    document_types: Mapping[str, DocumentType],
    instruction_loader: Callable[[str], str] | None = None,
    routine_loader: RoutineLoader | None = None,
) -> dict[str, Any]:
    """Parse a workflow definition JSON document, derive its port schemas, and validate it."""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("workflow definition must be a JSON object")
    return validate_workflow_definition_payload(
        derive_workflow_port_schemas(
            parsed,
            document_types,
            instruction_loader=instruction_loader,
            routine_loader=routine_loader,
        )
    )


def validate_workflow_definition_payload(payload: Any) -> dict[str, Any]:
    """Validate one workflow definition payload and return its normalized dict."""

    schema = load_schema("definition.workflow")
    schema_error = validate_against_schema(payload, schema)
    if schema_error:
        raise ValueError(f"schema validation failed: {schema_error}")

    definition = WorkflowDefinition.model_validate(payload)
    event_nodes = {
        node_id: node
        for node_id, node in definition.graph.nodes.items()
        if isinstance(node, WorkflowNodeEvent)
    }
    if not any(isinstance(node, WorkflowTriggerNode) for node in definition.graph.nodes.values()):
        raise ValueError("workflow graph must contain at least one event or cron trigger node")
    for node_id, node in definition.graph.nodes.items():
        if isinstance(node, WorkflowNodeCron) and (
            error := cron_error(node.expression, node.timezone)
        ):
            raise ValueError(f"cron node '{node_id}': {error}")
    for node_id, event_node in event_nodes.items():
        ports = workflow_node_output_ports(event_node)
        unknown_paths = [path for path in event_node.match if schema_at_path(ports, path) is None]
        if unknown_paths:
            raise ValueError(
                f"event node '{node_id}' match references unknown path(s) "
                f"{', '.join(unknown_paths)}"
            )
        document_fields = (ports.get("after") or {}).get("properties")
        if isinstance(document_fields, dict) and document_fields:
            unknown_fields = [
                field for field in event_node.changed_fields if field not in document_fields
            ]
            if unknown_fields:
                raise ValueError(
                    f"event node '{node_id}' changed_fields references unknown field(s) "
                    f"{', '.join(unknown_fields)}"
                )

    _validate_workflow_graph(definition)
    return definition.model_dump(mode="json", by_alias=True)


def ordered_workflow_node_ids(definition: WorkflowDefinition) -> list[str]:
    """Return a linear execution order when value dependencies form one chain."""

    outgoing: dict[str, list[str]] = defaultdict(list)
    inbound: dict[str, int] = dict.fromkeys(definition.graph.nodes, 0)
    for edge in definition.graph.edges:
        outgoing[edge.from_.node].append(edge.to.node)
        inbound[edge.to.node] = inbound.get(edge.to.node, 0) + 1

    event_roots = [
        node_id
        for node_id, node in definition.graph.nodes.items()
        if isinstance(node, WorkflowTriggerNode) and inbound[node_id] == 0
    ]
    if len(event_roots) != 1:
        raise ValueError("ordered nodes are unavailable without exactly one trigger root")
    ordered, seen = [], set()
    next_nodes = outgoing.get(event_roots[0], [])
    if len(next_nodes) > 1:
        raise ValueError("ordered nodes are unavailable for a branched workflow")
    cursor = next_nodes[0] if next_nodes else None
    while cursor is not None:
        if cursor in seen:
            raise ValueError("workflow graph must be acyclic")
        ordered.append(cursor)
        seen.add(cursor)
        next_nodes = outgoing.get(cursor, [])
        if len(next_nodes) > 1:
            raise ValueError("ordered nodes are unavailable for a branched workflow")
        cursor = next_nodes[0] if next_nodes else None
    return ordered


def coerce_workflow_definition(payload: Mapping[str, Any]) -> WorkflowDefinition:
    """Validate and coerce a stored payload to ``WorkflowDefinition``."""

    return WorkflowDefinition.model_validate(validate_workflow_definition_payload(dict(payload)))


def coerce_workflow_run(payload: Mapping[str, Any]) -> WorkflowRun:
    """Validate and coerce a stored payload to ``WorkflowRun``."""

    return WorkflowRun.model_validate(dict(payload))


class WorkflowInvokeRequest(BaseModel):
    """Durable request for the backend executor to start one workflow run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    workflow_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    ancestry: list[str] = Field(default_factory=list)
    requested_at: str


def parse_workflow_invoke_request_document(
    payload: Mapping[str, Any],
) -> WorkflowInvokeRequest:
    """Validate and coerce a stored payload to WorkflowInvokeRequest."""

    return WorkflowInvokeRequest.model_validate(dict(payload))


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

    if isinstance(node, WorkflowNodeData | WorkflowNodeEvent | WorkflowNodeCron):
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


def _schema_types_compatible(source: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    """Structural compatibility: source JSON types contained in target types,
    and matching title if both declare one."""

    source_type = source.get("type")
    target_type = target.get("type")
    if source_type and target_type:
        source_types = {source_type} if isinstance(source_type, str) else set(source_type)
        target_types = {target_type} if isinstance(target_type, str) else set(target_type)
        if "number" in target_types:
            target_types.add("integer")  # every JSON integer is a number
        if not source_types <= target_types:
            return False
    source_title = source.get("title")
    target_title = target.get("title")
    return not (source_title and target_title and source_title != target_title)


def _validate_workflow_graph(definition: WorkflowDefinition) -> None:
    node_ids = set(definition.graph.nodes)
    if not node_ids:
        raise ValueError("workflow graph must contain at least one node")

    for edge in definition.graph.edges:
        if edge.from_.node not in node_ids:
            raise ValueError(f"edge references unknown source node '{edge.from_.node}'")
        if edge.to.node not in node_ids:
            raise ValueError(f"edge references unknown target node '{edge.to.node}'")

    _validate_data_edges(definition)

    for node_id, node in definition.graph.nodes.items():
        if isinstance(node, WorkflowNodeCondition) and not node.expression.strip():
            raise ValueError(f"condition node '{node_id}' is missing an expression")
        elif isinstance(node, WorkflowNodeApproval) and not node.message.strip():
            raise ValueError(f"approval node '{node_id}' is missing a message")
        elif isinstance(node, WorkflowNodeFormat) and not node.template.strip():
            raise ValueError(f"format node '{node_id}' is missing a template")
        elif isinstance(node, WorkflowNodeExtract | WorkflowNodeAgent) and not (
            node.instruction or node.template
        ):
            raise ValueError(f"{node.kind} node '{node_id}' is missing an instruction or template")


def _validate_data_edges(definition: WorkflowDefinition) -> None:
    nodes = definition.graph.nodes
    inbound: dict[str, int] = dict.fromkeys(nodes, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)
    seen_targets: set[tuple[str, str]] = set()

    for edge in definition.graph.edges:
        source = nodes[edge.from_.node]
        target = nodes[edge.to.node]
        output_ports = workflow_node_output_ports(source)
        if edge.from_.port not in output_ports:
            raise ValueError(
                f"edge source '{edge.from_.node}' has no output port '{edge.from_.port}'"
            )
        input_ports = workflow_node_input_ports(target)
        if edge.to.port not in input_ports:
            raise ValueError(f"edge target '{edge.to.node}' has no input port '{edge.to.port}'")
        target_key = (edge.to.node, edge.to.port)
        if target_key in seen_targets:
            raise ValueError(
                f"input port '{edge.to.port}' on node '{edge.to.node}' has more than one edge"
            )
        seen_targets.add(target_key)
        if not _schema_types_compatible(output_ports[edge.from_.port], input_ports[edge.to.port]):
            raise ValueError(
                f"edge from '{edge.from_.node}.{edge.from_.port}' to "
                f"'{edge.to.node}.{edge.to.port}' has incompatible types"
            )
        inbound[edge.to.node] += 1
        adjacency[edge.from_.node].append(edge.to.node)

    for node_id, node in nodes.items():
        if isinstance(node, WorkflowTriggerNode) and inbound[node_id]:
            raise ValueError(f"trigger node '{node_id}' cannot have inbound edges")
        if (
            not isinstance(node, WorkflowNodeData | WorkflowNodeEvent | WorkflowNodeCron)
            and not inbound[node_id]
        ):
            raise ValueError(f"executable node '{node_id}' has no inbound connection")
        input_schema = getattr(node, "input_schema", None) or {}
        for port in input_schema.get("required") or []:
            if (node_id, port) not in seen_targets:
                raise ValueError(f"required input '{port}' on node '{node_id}' has no edge")

    _assert_acyclic(set(nodes), adjacency)


def _assert_acyclic(node_ids: set[str], adjacency: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise ValueError("workflow graph must be acyclic")
        visiting.add(node_id)
        for child_id in adjacency.get(node_id, []):
            visit(child_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_ids:
        visit(node_id)
