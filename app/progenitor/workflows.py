"""Shared workflow definitions, validation, and helpers."""

from __future__ import annotations

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
from .schemas import load_schema
from .structured import validate_against_schema
from .utils import utcnow_isoformat

# Knowledge indexer events, dispatched by the backend indexer when a base finishes
# or fails indexing. Filtered by the trigger's key field (exact base id).
KNOWLEDGE_INDEX_EVENT_TYPES = ("knowledge.index.completed", "knowledge.index.failed")

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
    *KNOWLEDGE_INDEX_EVENT_TYPES,
)
WORKFLOW_DOCUMENT_TRIGGER_ACTIONS = ("upserted", "deleted")
WORKFLOW_DEFAULT_EVENT_TYPE = WORKFLOW_ALLOWED_EVENT_TYPES[0]

WORKFLOW_DEFAULT_AGENT_NAME = "progenitor"
WORKFLOW_VERSION = 1
# Max chained Progenitor-initiated workflow invocations.
MAX_WORKFLOW_INVOKE_DEPTH = 3

# Kanban columns a kanban.card.moved trigger can filter on. Mirrors the fixed
# pipeline in app/backend/ui_routes.py:_kanban_default_columns().
WORKFLOW_KANBAN_COLUMNS = ("backlog", "plan", "build", "validate", "done")

WorkflowRunStatus = Literal["queued", "running", "paused", "completed", "failed"]
WorkflowRunSource = Literal["kanban", "manual", "progenitor"]


class WorkflowTrigger(BaseModel):
    """Workflow entrypoint definition."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["event"] = "event"
    event_type: str
    from_column: str | None = None
    to_column: str | None = None
    # document.changed filters: each is a wildcard when unset. key_prefix
    # matches a document kind (see document_kinds.DOCUMENT_KINDS), key an
    # exact document key, action the write kind (upserted vs deleted).
    # For knowledge.index.* events, key matches the base id (payload.key).
    key_prefix: str | None = None
    key: str | None = None
    action: Literal["upserted", "deleted"] | None = None


class WorkflowNodeEvent(BaseModel):
    """Event node for the workflow canvas."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event"] = "event"
    title: str = "Event"


class WorkflowNodePrompt(BaseModel):
    """Prompt node that runs through the backend SSE path."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prompt"] = "prompt"
    title: str = "Prompt"
    prompt: str
    agent_name: str = WORKFLOW_DEFAULT_AGENT_NAME
    role: str | None = None


WORKFLOW_MESSAGE_TYPES = ("say_to_user", "notify_user", "ask_user")
WORKFLOW_NOTIFICATION_LEVELS = ("info", "warning", "error", "success")


class WorkflowNodeUser(BaseModel):
    """User-message node: delivers a message to the user via an agent tool."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user"] = "user"
    title: str = "User"
    message_type: Literal["say_to_user", "notify_user", "ask_user"] = "say_to_user"
    message: str
    notification_title: str | None = None
    level: Literal["info", "warning", "error", "success"] = "info"


class WorkflowNodeCode(BaseModel):
    """Code node that asks the agent to run a code snippet through the SSE path."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["code"] = "code"
    title: str = "Code"
    language: str = "javascript"
    content: str


class WorkflowNodeCondition(BaseModel):
    """Branch node evaluated against the workflow input and previous output."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["condition"] = "condition"
    title: str = "Condition"
    expression: str


class WorkflowNodeApproval(BaseModel):
    """Human approval gate that pauses a workflow run."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval"] = "approval"
    title: str = "Approval"
    message: str


class WorkflowNodeInbox(BaseModel):
    """Send a durable message to the user's inbox."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["inbox"] = "inbox"
    title: str = "Send to Inbox"
    message_title: str = "Workflow message"
    message: str
    level: Literal["info", "warning", "error", "success"] = "info"


class WorkflowNodeAgent(BaseModel):
    """Call a configured A2A or ACP agent and pass its result downstream."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["agent"] = "agent"
    title: str = "Call Agent"
    transport: Literal["a2a", "acp"] = "a2a"
    agent_name: str
    request: str
    timeout_seconds: int = Field(default=300, ge=1, le=3600)


WorkflowNode = Annotated[
    WorkflowNodeEvent
    | WorkflowNodePrompt
    | WorkflowNodeUser
    | WorkflowNodeCode
    | WorkflowNodeCondition
    | WorkflowNodeApproval
    | WorkflowNodeInbox
    | WorkflowNodeAgent,
    Field(discriminator="kind"),
]


class WorkflowEdgeEndpoint(BaseModel):
    """One end of a workflow edge."""

    model_config = ConfigDict(extra="forbid")

    node: str
    port: str = "default"


class WorkflowEdge(BaseModel):
    """Directed connection between two nodes."""

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
    trigger: WorkflowTrigger
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
    source: WorkflowRunSource = "manual"
    ancestry: list[str] = Field(default_factory=list)
    last_output: str | None = None
    last_error: str | None = None
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

    return WorkflowListItem(
        id=definition.id,
        title=definition.title,
        description=definition.description,
        enabled=definition.enabled,
        paused=definition.paused,
        version=definition.version,
        event_type=definition.trigger.event_type,
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
    """Return the minimal valid workflow document for v1."""

    created_at = utcnow_isoformat()
    resolved_id = workflow_id or f"workflow_{uuid.uuid4().hex[:10]}"
    event_node_id = f"event_{uuid.uuid4().hex[:8]}"
    prompt_node_id = f"prompt_{uuid.uuid4().hex[:8]}"
    return {
        "id": resolved_id,
        "title": title,
        "description": description,
        "enabled": True,
        "paused": False,
        "version": WORKFLOW_VERSION,
        "trigger": {
            "type": "event",
            "event_type": event_type,
        },
        "graph": {
            "nodes": {
                event_node_id: {
                    "kind": "event",
                    "title": "Event",
                },
                prompt_node_id: {
                    "kind": "prompt",
                    "title": "Prompt",
                    "prompt": "Handle this workflow event.",
                    "agent_name": WORKFLOW_DEFAULT_AGENT_NAME,
                    "role": None,
                },
            },
            "edges": [
                {
                    "from": {"node": event_node_id, "port": "default"},
                    "to": {"node": prompt_node_id, "port": "default"},
                }
            ],
        },
        "ui": {
            "nodes": {
                event_node_id: {
                    "position": {"x": 96, "y": 160},
                    "size": {
                        "data": {"width": 320, "height": 120},
                        "settings": {"width": 320, "height": 190},
                    },
                },
                prompt_node_id: {
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
    if definition.version != WORKFLOW_VERSION:
        raise ValueError(f"workflow version must be {WORKFLOW_VERSION}")
    if definition.trigger.event_type not in WORKFLOW_ALLOWED_EVENT_TYPES:
        raise ValueError(
            f"unsupported workflow trigger event type: {definition.trigger.event_type}"
        )
    if (
        definition.trigger.from_column is not None or definition.trigger.to_column is not None
    ) and definition.trigger.event_type != "kanban.card.moved":
        raise ValueError(
            "from_column and to_column filters are only valid for kanban.card.moved triggers"
        )
    trigger = definition.trigger
    if trigger.event_type in KNOWLEDGE_INDEX_EVENT_TYPES:
        if trigger.key_prefix is not None or trigger.action is not None:
            raise ValueError(
                "knowledge.index.* triggers filter only by key (the knowledge base id)"
            )
    elif trigger.event_type != "document.changed" and (
        trigger.key_prefix is not None or trigger.key is not None or trigger.action is not None
    ):
        raise ValueError(
            "key_prefix, key, and action filters are only valid for document.changed triggers"
        )

    _validate_workflow_graph(definition)
    return definition.model_dump(mode="json", by_alias=True)


def ordered_workflow_node_ids(definition: WorkflowDefinition) -> list[str]:
    """Return the single executable path for a valid v1 workflow."""

    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = dict.fromkeys(definition.graph.nodes, 0)
    for edge in definition.graph.edges:
        source_id = edge.from_.node
        target_id = edge.to.node
        outgoing[source_id].append(target_id)
        indegree[target_id] = indegree.get(target_id, 0) + 1

    event_node_id = next(
        node_id
        for node_id, node in definition.graph.nodes.items()
        if isinstance(node, WorkflowNodeEvent)
    )

    ordered = [event_node_id]
    seen = {event_node_id}
    cursor = event_node_id
    while outgoing.get(cursor):
        next_nodes = outgoing[cursor]
        if len(next_nodes) > 1:
            raise ValueError("workflow graph must follow a single outgoing path in v1")
        cursor = next_nodes[0]
        if cursor in seen:
            raise ValueError("workflow graph must be acyclic")
        ordered.append(cursor)
        seen.add(cursor)
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


def _validate_workflow_graph(definition: WorkflowDefinition) -> None:
    node_ids = set(definition.graph.nodes)
    if not node_ids:
        raise ValueError("workflow graph must contain at least one node")

    inbound: dict[str, int] = dict.fromkeys(node_ids, 0)
    outbound: dict[str, int] = dict.fromkeys(node_ids, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in definition.graph.edges:
        source_id = edge.from_.node
        target_id = edge.to.node
        if source_id not in node_ids:
            raise ValueError(f"edge references unknown source node '{source_id}'")
        if target_id not in node_ids:
            raise ValueError(f"edge references unknown target node '{target_id}'")
        outbound[source_id] += 1
        inbound[target_id] += 1
        adjacency[source_id].append(target_id)

    event_nodes = [
        node_id
        for node_id, node in definition.graph.nodes.items()
        if isinstance(node, WorkflowNodeEvent)
    ]
    if len(event_nodes) != 1:
        raise ValueError("workflow graph must contain exactly one event node in v1")

    for event_node_id in event_nodes:
        if inbound[event_node_id] != 0:
            raise ValueError("event nodes cannot have inbound edges")
        if outbound[event_node_id] > 1:
            raise ValueError("event nodes may have at most one outbound edge in v1")

    _assert_acyclic(node_ids, adjacency)

    for node_id, node in definition.graph.nodes.items():
        branching = isinstance(node, WorkflowNodeCondition | WorkflowNodeApproval)
        if not branching and isinstance(
            node,
            WorkflowNodePrompt
            | WorkflowNodeUser
            | WorkflowNodeCode
            | WorkflowNodeInbox
            | WorkflowNodeAgent,
        ):
            if inbound[node_id] > 1:
                raise ValueError(f"node '{node_id}' may have at most one inbound edge in v1")
            if outbound[node_id] > 1:
                raise ValueError(f"node '{node_id}' may have at most one outbound edge in v1")
        elif branching:
            if inbound[node_id] > 1:
                raise ValueError(f"node '{node_id}' may have at most one inbound edge in v1")
            if outbound[node_id] > 2:
                raise ValueError(f"node '{node_id}' may have at most two outbound edges in v1")
            expected_ports = (
                {"true", "false"}
                if isinstance(node, WorkflowNodeCondition)
                else {
                    "approved",
                    "rejected",
                }
            )
            ports = [
                edge.from_.port for edge in definition.graph.edges if edge.from_.node == node_id
            ]
            if any(port not in expected_ports for port in ports) or len(ports) != len(set(ports)):
                expected = ", ".join(sorted(expected_ports))
                raise ValueError(
                    f"{node.kind} node '{node_id}' edges must use distinct ports from: {expected}"
                )
        if isinstance(node, WorkflowNodePrompt):
            if not node.prompt.strip():
                raise ValueError(f"prompt node '{node_id}' is missing a prompt")
            if not str(node.agent_name or "").strip():
                raise ValueError(f"prompt node '{node_id}' is missing an agent_name")
        elif isinstance(node, WorkflowNodeUser):
            if not node.message.strip():
                raise ValueError(f"user node '{node_id}' is missing a message")
            if (
                node.message_type == "notify_user"
                and not str(node.notification_title or "").strip()
            ):
                raise ValueError(f"user node '{node_id}' is missing a notification_title")
        elif isinstance(node, WorkflowNodeCode):
            if not node.content.strip():
                raise ValueError(f"code node '{node_id}' is missing content")
            if not str(node.language or "").strip():
                raise ValueError(f"code node '{node_id}' is missing a language")
        elif isinstance(node, WorkflowNodeCondition) and not node.expression.strip():
            raise ValueError(f"condition node '{node_id}' is missing an expression")
        elif isinstance(node, WorkflowNodeApproval) and not node.message.strip():
            raise ValueError(f"approval node '{node_id}' is missing a message")
        elif isinstance(node, WorkflowNodeInbox) and not node.message.strip():
            raise ValueError(f"inbox node '{node_id}' is missing a message")
        elif isinstance(node, WorkflowNodeAgent) and (
            not node.agent_name.strip() or not node.request.strip()
        ):
            raise ValueError(f"agent node '{node_id}' is missing agent_name or request")


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
