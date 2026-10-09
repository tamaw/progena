"""Shared workflow definitions, validation, and helpers."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .document_kinds import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from .schemas import load_schema
from .structured import validate_against_schema
from .utils import utcnow_isoformat

WORKFLOW_ALLOWED_EVENT_TYPES = (
    "kanban.card.created",
    "kanban.card.updated",
    "kanban.card.moved",
    "kanban.card.deleted",
    "kanban.card.status_changed",
    # Generic document-change event, fired for any tracked document kind
    # (see document_kinds.py) via the store table's LISTEN/NOTIFY trigger.
    # Filtered by the trigger's key_prefix/key/action fields below, not by
    # from_column/to_column (kanban-only).
    "document.changed",
)
WORKFLOW_DOCUMENT_TRIGGER_ACTIONS = ("upserted", "deleted")
WORKFLOW_DEFAULT_EVENT_TYPE = WORKFLOW_ALLOWED_EVENT_TYPES[0]

WORKFLOW_VERSION = 3
# Max chained Progenitor-initiated workflow invocations.
MAX_WORKFLOW_INVOKE_DEPTH = 3

# Kanban columns a kanban.card.moved trigger can filter on. Mirrors the fixed
# pipeline in app/backend/ui_routes.py:_kanban_default_columns().
WORKFLOW_KANBAN_COLUMNS = ("backlog", "plan", "build", "validate", "done")

WorkflowRunStatus = Literal["queued", "running", "paused", "completed", "failed"]
WorkflowRunSource = Literal["kanban", "document", "manual", "progenitor"]

_JSON_OBJECT_SCHEMA = {"type": "object"}
_KANBAN_CARD_SCHEMA = {
    "title": "Card",
    "type": "object",
    "required": [
        "id",
        "title",
        "summary",
        "description",
        "column_id",
        "status",
        "agent_name",
        "owner",
        "labels",
        "created_at",
        "updated_at",
        "position",
        "metadata",
    ],
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "description": {"type": "string"},
        "column_id": {"type": "string"},
        "status": {"type": "string"},
        "agent_name": {"type": "string"},
        "owner": {"type": "string"},
        "project_id": {"type": ["string", "null"]},
        "labels": {"type": "array", "items": {"type": "string"}},
        "spec_path": {"type": ["string", "null"]},
        "branch_name": {"type": ["string", "null"]},
        "pr_url": {"type": ["string", "null"]},
        "started_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
        "last_error": {"type": ["string", "null"]},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
        "position": {"type": "integer"},
        "metadata": _JSON_OBJECT_SCHEMA,
    },
}
_KANBAN_TASK_SCHEMA = {
    "title": "Task",
    "type": "object",
    "required": ["path", "format", "content"],
    "properties": {
        "path": {"type": "string"},
        "format": {"type": "string"},
        "updated_at": {"type": ["string", "null"]},
        "content": {"type": "string"},
    },
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
_KANBAN_POSITION_SCHEMA = {
    "title": "ColumnPosition",
    "type": "object",
    "required": ["column_id", "position"],
    "properties": {
        "column_id": {"type": ["string", "null"]},
        "position": {"type": ["integer", "null"]},
    },
}


def workflow_trigger_output_schema(event_type: str) -> dict[str, Any]:
    """Return the canonical, flat dataflow contract for one event type.

    Every property is a directly connectable top-level output port; there is
    no ``payload``/envelope wrapper and no ``oneOf`` branching by source.
    """

    if event_type == "document.changed":
        return {
            "title": "Event",
            "type": "object",
            "required": ["event_type", "key_prefix", "key", "action"],
            "properties": {
                "event_type": {"type": "string", "const": event_type},
                "timestamp": {"type": "string"},
                "key_prefix": {"type": "string"},
                "key": {"type": "string"},
                "action": {"type": ["string", "null"]},
            },
        }

    properties: dict[str, Any] = {
        "event_type": {"type": "string", "const": event_type},
        "timestamp": {"type": "string"},
        "card": _KANBAN_CARD_SCHEMA,
        "task": _KANBAN_TASK_SCHEMA,
    }
    # Only the envelope fields are required: a manual/operator-triggered test
    # run may omit card/task, while real kanban dispatch always supplies the
    # full typed payload below.
    required = ["event_type", "timestamp"]
    if event_type in {"kanban.card.updated", "kanban.card.moved", "kanban.card.status_changed"}:
        properties["previous_card"] = _KANBAN_CARD_SCHEMA
    if event_type == "kanban.card.updated":
        properties["changed_fields"] = {"type": "array", "items": {"type": "string"}}
    elif event_type == "kanban.card.moved":
        properties["from"] = _KANBAN_POSITION_SCHEMA
        properties["to"] = _KANBAN_POSITION_SCHEMA
    elif event_type == "kanban.card.status_changed":
        properties["from_status"] = {"type": ["string", "null"]}
        properties["to_status"] = {"type": ["string", "null"]}

    return {
        "title": "Event",
        "type": "object",
        "required": required,
        "properties": properties,
    }


class WorkflowNodeEvent(BaseModel):
    """Graph-native event entrypoint with its own event filters."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event"] = "event"
    title: str = "Kanban event"
    event_type: str
    from_column: str | None = None
    to_column: str | None = None
    # document.changed filters: each is a wildcard when unset. key_prefix
    # matches a document kind (see document_kinds.DOCUMENT_KINDS), key an
    # exact document key, action the write kind (upserted vs deleted).
    key_prefix: str | None = None
    key: str | None = None
    action: Literal["upserted", "deleted"] | None = None
    output_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def apply_canonical_output_schema(self) -> WorkflowNodeEvent:
        self.output_schema = workflow_trigger_output_schema(self.event_type)
        return self


class WorkflowNodeData(BaseModel):
    """A literal typed value node with no execution behavior."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["data"] = "data"
    title: str = "Data"
    value: Any = None
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


# Data nodes are literal source values; every other node either starts a run
# (event) or runs only when an inbound data edge supplies an input.
WorkflowControlNode = (
    WorkflowNodeEvent
    | WorkflowNodeAgent
    | WorkflowNodeMcp
    | WorkflowNodeCondition
    | WorkflowNodeApproval
    | WorkflowNodeHumanInput
)

WorkflowNode = Annotated[
    WorkflowNodeEvent
    | WorkflowNodeData
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
    event_type: str
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
        event_type=event_node.event_type if event_node else "",
        node_count=len(definition.graph.nodes),
        updated_at=definition.updated_at,
        created_at=definition.created_at,
    )


def default_workflow_definition(
    *,
    workflow_id: str | None = None,
    title: str = "Untitled workflow",
    description: str = "",
    event_type: str = WORKFLOW_DEFAULT_EVENT_TYPE,
) -> dict[str, Any]:
    """Return the minimal valid graph-native workflow document."""

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
                    "title": "Kanban event",
                    "event_type": event_type,
                    "from_column": None,
                    "to_column": None,
                },
                agent_node_id: {
                    "kind": "agent",
                    "title": "Agent",
                    "message": "Handle this workflow event.",
                    "input_schema": {
                        "type": "object",
                        "required": ["event_type"],
                        "properties": {"event_type": {"type": "string"}},
                    },
                    "output_schema": {"type": "object", "properties": {}},
                },
            },
            "edges": [
                {
                    "from": {"node": event_node_id, "port": "event_type"},
                    "to": {"node": agent_node_id, "port": "event_type"},
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


def validate_workflow_definition_document(content: str) -> dict[str, Any]:
    """Parse and validate a workflow definition JSON document."""

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    return validate_workflow_definition_payload(parsed)


def validate_workflow_definition_payload(payload: Any) -> dict[str, Any]:
    """Validate one workflow definition payload and return its normalized dict."""

    schema = load_schema("definition.workflow")
    schema_error = validate_against_schema(payload, schema)
    if schema_error:
        raise ValueError(f"schema validation failed: {schema_error}")

    definition = WorkflowDefinition.model_validate(payload)
    event_nodes = [
        node for node in definition.graph.nodes.values() if isinstance(node, WorkflowNodeEvent)
    ]
    if not event_nodes:
        raise ValueError("workflow graph must contain at least one event node")
    for event_node in event_nodes:
        if event_node.event_type not in WORKFLOW_ALLOWED_EVENT_TYPES:
            raise ValueError(f"unsupported workflow event type: {event_node.event_type}")
        if (
            event_node.from_column is not None or event_node.to_column is not None
        ) and event_node.event_type != "kanban.card.moved":
            raise ValueError(
                "from_column and to_column filters are only valid for kanban.card.moved events"
            )
        if (
            event_node.key_prefix is not None
            or event_node.key is not None
            or event_node.action is not None
        ) and event_node.event_type != "document.changed":
            raise ValueError(
                "key_prefix, key, and action filters are only valid for document.changed events"
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
