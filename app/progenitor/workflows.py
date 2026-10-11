"""Shared workflow definitions, validation, and helpers."""

from __future__ import annotations

import copy
import json
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .document_kinds import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from .document_types import DOCUMENT_OPERATIONS, DocumentOperation, DocumentType
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
WorkflowRunSource = Literal["document", "manual", "progenitor"]

_JSON_OBJECT_SCHEMA = {"type": "object"}
_ANY_VALUE_SCHEMA: dict[str, Any] = {}
_TRIGGER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["trigger"],
    "properties": {"trigger": _ANY_VALUE_SCHEMA},
}
_MCP_TOOL_SCHEMA = {
    "title": "McpTool",
    "type": "object",
    "required": ["server_id", "tool_name"],
    "properties": {
        "server_id": {"type": "string"},
        "tool_name": {"type": "string"},
    },
}
# Root keywords that only make sense at a schema's own root. ``$id`` would
# re-base ``$ref`` resolution, so it is dropped; definitions are hoisted to the
# composed port schema's root so internal ``#/$defs/...`` refs keep resolving.
_SCHEMA_ROOT_ONLY_KEYS = ("$schema", "$id")
_SCHEMA_DEFINITION_KEYS = ("$defs", "definitions")


def _embed_document_schema(
    document_schema: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Split one document schema into (embeddable schema, properties, root definitions)."""

    if not document_schema:
        return dict(_JSON_OBJECT_SCHEMA), {}, {}
    embedded = copy.deepcopy(dict(document_schema))
    for key in _SCHEMA_ROOT_ONLY_KEYS:
        embedded.pop(key, None)
    definitions = {
        key: embedded.pop(key)
        for key in _SCHEMA_DEFINITION_KEYS
        if isinstance(embedded.get(key), dict)
    }
    properties = embedded.get("properties")
    return embedded, dict(properties) if isinstance(properties, dict) else {}, definitions


def _flattened_ports(prefix: str, properties: Mapping[str, Any]) -> dict[str, Any]:
    return {f"{prefix}.{name}": copy.deepcopy(schema) for name, schema in properties.items()}


def document_event_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Return the flat output contract of an event node on ``document_type``.

    ``before``/``after`` carry the whole document; every top-level document
    property is also its own directly connectable ``before.<p>``/``after.<p>``
    port. Sides absent for an operation (``before`` on created, ``after`` on
    deleted) are simply not produced.
    """

    embedded, properties, definitions = _embed_document_schema(document_schema)
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
            "after": copy.deepcopy(embedded),
            **_flattened_ports("before", properties),
            **_flattened_ports("after", properties),
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
    embedded, properties, definitions = _embed_document_schema(document_schema)
    return {
        "title": f"{document_type.label} read",
        "type": "object",
        "required": ["found", "key", "id"],
        "properties": {
            "found": {"type": "boolean"},
            "key": {"type": "string"},
            "id": {"type": "string"},
            "document": embedded,
            **_flattened_ports("document", properties),
        },
        **definitions,
    }


def document_query_input_schema(
    document_type: DocumentType,
    document_schema: Mapping[str, Any] | None,
    filter_fields: list[str],
) -> dict[str, Any]:
    if not filter_fields:
        return copy.deepcopy(_TRIGGER_INPUT_SCHEMA)
    _embedded, properties, definitions = _embed_document_schema(document_schema)
    if properties:
        unknown = [field for field in filter_fields if field not in properties]
        if unknown:
            raise ValueError(
                f"document type '{document_type.id}' has no field(s) {', '.join(unknown)}"
            )
    return {
        "type": "object",
        "required": list(filter_fields),
        "properties": {
            field: copy.deepcopy(properties.get(field, _ANY_VALUE_SCHEMA))
            for field in filter_fields
        },
        **definitions,
    }


def document_query_output_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> dict[str, Any]:
    embedded, _properties, definitions = _embed_document_schema(document_schema)
    return {
        "title": f"{document_type.label} query",
        "type": "object",
        "required": ["items", "keys", "count"],
        "properties": {
            "items": {"type": "array", "items": embedded},
            "keys": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer"},
        },
        **definitions,
    }


def document_changed_fields(before: Any, after: Any) -> list[str]:
    """Top-level fields whose value differs between two document payloads."""

    previous = before if isinstance(before, Mapping) else {}
    current = after if isinstance(after, Mapping) else {}
    return sorted(
        field
        for field in set(previous) | set(current)
        if field not in previous or field not in current or previous[field] != current[field]
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
    for side, value in (("before", before), ("after", after)):
        if value is None:
            continue
        output[side] = value
        if isinstance(value, Mapping):
            output.update({f"{side}.{name}": field for name, field in value.items()})
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
        if isinstance(document, Mapping):
            output.update({f"document.{name}": field for name, field in document.items()})
    return output


class WorkflowNodeEvent(BaseModel):
    """Entrypoint fired by create/update/delete events on one document type.

    Filters are all optional: ``key`` pins one document, ``operations`` limits
    the op (empty = any), ``changed_fields`` requires at least one listed
    top-level field to differ, and ``match`` requires equality on output ports
    (e.g. ``{"after.column_id": "done"}``).
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
        return all(event.get(port) == value for port, value in self.match.items())


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
    ``apply_document_type_schemas``).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_read"] = "document_read"
    title: str = "Read document"
    document_type: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeDocumentQuery(BaseModel):
    """Deterministic equality query over a collection document type.

    Each ``filter_fields`` entry becomes a required input port whose value must
    equal that top-level document field; results are ordered by key.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["document_query"] = "document_query"
    title: str = "Query documents"
    document_type: str = Field(min_length=1)
    filter_fields: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=MAX_WORKFLOW_QUERY_LIMIT)
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeAgent(BaseModel):
    """A generic Progenitor-backed step: typed input, message, typed output."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agent"] = "agent"
    title: str = "Agent"
    message: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


class WorkflowNodeMcp(BaseModel):
    """Deterministic MCP tool invocation with a typed tool selection input."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["mcp"] = "mcp"
    title: str = "MCP Tool"
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "required": ["tool", "arguments"],
            "properties": {
                "tool": dict(_MCP_TOOL_SCHEMA),
                "arguments": dict(_JSON_OBJECT_SCHEMA),
            },
        }
    )
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


class WorkflowNodeHumanInput(BaseModel):
    """Pause gate that resumes with an arbitrary typed human-supplied value."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["human_input"] = "human_input"
    title: str = "Human Input"
    message: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


WorkflowNode = Annotated[
    WorkflowNodeEvent
    | WorkflowNodeData
    | WorkflowNodeDocumentRead
    | WorkflowNodeDocumentQuery
    | WorkflowNodeAgent
    | WorkflowNodeMcp
    | WorkflowNodeCondition
    | WorkflowNodeApproval
    | WorkflowNodeHumanInput,
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
    return WorkflowListItem(
        id=definition.id,
        title=definition.title,
        description=definition.description,
        enabled=definition.enabled,
        paused=definition.paused,
        version=definition.version,
        document_type=event_node.document_type if event_node else "",
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

    Event port schemas are filled in by ``apply_document_type_schemas`` on save.
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
                    "message": "Handle this workflow event.",
                    "input_schema": {
                        "type": "object",
                        "required": ["key"],
                        "properties": {"key": {"type": "string"}},
                    },
                    "output_schema": {"type": "object", "properties": {}},
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


def apply_document_type_schemas(
    payload: Mapping[str, Any], document_types: Mapping[str, DocumentType]
) -> dict[str, Any]:
    """Return ``payload`` with every document node's port schemas derived from its type.

    Run on every save path (backend routes and the runtime's virtual
    ``/workflows/`` mount) so ports always reflect the registered type schema,
    whatever schemas the writer sent.
    """

    result = copy.deepcopy(dict(payload))
    graph = result.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    if not isinstance(nodes, dict):
        return result
    schemas: dict[str, dict[str, Any] | None] = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("kind") not in {
            "event",
            "document_read",
            "document_query",
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
        else:
            if not document_type.collection:
                raise ValueError(
                    f"document_query node '{node_id}' needs a collection document type, "
                    f"not single document '{document_type.id}'"
                )
            filter_fields = node.get("filter_fields") or []
            node["input_schema"] = document_query_input_schema(
                document_type, document_schema, [str(field) for field in filter_fields]
            )
            node["output_schema"] = document_query_output_schema(document_type, document_schema)
    return result


def validate_workflow_definition_document(
    content: str, document_types: Mapping[str, DocumentType]
) -> dict[str, Any]:
    """Parse a workflow definition JSON document, derive its port schemas, and validate it."""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("workflow definition must be a JSON object")
    return validate_workflow_definition_payload(apply_document_type_schemas(parsed, document_types))


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
    if not event_nodes:
        raise ValueError("workflow graph must contain at least one event node")
    for node_id, event_node in event_nodes.items():
        ports = workflow_node_output_ports(event_node)
        unknown_ports = [port for port in event_node.match if port not in ports]
        if unknown_ports:
            raise ValueError(
                f"event node '{node_id}' match references unknown port(s) "
                f"{', '.join(unknown_ports)}"
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
        if isinstance(node, WorkflowNodeEvent) and inbound[node_id] == 0
    ]
    if len(event_roots) != 1:
        raise ValueError("ordered nodes are unavailable without exactly one event root")
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

    if isinstance(node, WorkflowNodeData | WorkflowNodeEvent):
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
    """Structural compatibility: same JSON type, and matching title if both declare one."""

    source_type = source.get("type")
    target_type = target.get("type")
    if source_type and target_type and source_type != target_type:
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
        elif isinstance(node, WorkflowNodeHumanInput) and not node.message.strip():
            raise ValueError(f"human_input node '{node_id}' is missing a message")
        elif isinstance(node, WorkflowNodeAgent) and not node.message.strip():
            raise ValueError(f"agent node '{node_id}' is missing a message")


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
        if isinstance(node, WorkflowNodeEvent) and inbound[node_id]:
            raise ValueError("event nodes cannot have inbound edges")
        if not isinstance(node, WorkflowNodeData | WorkflowNodeEvent) and not inbound[node_id]:
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
