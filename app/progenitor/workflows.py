"""Shared workflow definitions, validation, and helpers."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .schemas import load_schema
from .structured import validate_against_schema
from .utils import utcnow_isoformat

WORKFLOW_DEFINITION_KEY_PREFIX = "workflow:"
WORKFLOW_RUN_KEY_PREFIX = "workflow_run:"
WORKFLOW_QUEUE_KEY_PREFIX = "workflow_queue:"
WORKFLOW_INVOKE_REQUEST_KEY_PREFIX = "workflow_invoke_request:"

WORKFLOW_ALLOWED_EVENT_TYPES = (
    "kanban.card.created",
    "kanban.card.updated",
    "kanban.card.moved",
    "kanban.card.deleted",
    "kanban.card.status_changed",
)
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

_JSON_OBJECT_SCHEMA = {"type": "object"}
_KANBAN_CARD_SCHEMA = {
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
    "type": "object",
    "required": ["path", "format", "content"],
    "properties": {
        "path": {"type": "string"},
        "format": {"type": "string"},
        "updated_at": {"type": ["string", "null"]},
        "content": {"type": "string"},
    },
}


def workflow_trigger_output_schema(event_type: str) -> dict[str, Any]:
    """Return the canonical dataflow contract for one workflow trigger type."""
    payload_properties: dict[str, Any] = {
        "event_type": {"const": event_type},
        "timestamp": {"type": "string"},
        "source": {"const": "kanban"},
        "card": _KANBAN_CARD_SCHEMA,
        "task": _KANBAN_TASK_SCHEMA,
    }
    payload_required = ["event_type", "timestamp", "source", "card", "task"]
    if event_type in {"kanban.card.updated", "kanban.card.moved", "kanban.card.status_changed"}:
        payload_properties["previous_card"] = _KANBAN_CARD_SCHEMA
        payload_required.append("previous_card")
    if event_type == "kanban.card.updated":
        payload_properties["changed_fields"] = {"type": "array", "items": {"type": "string"}}
        payload_required.append("changed_fields")
    elif event_type == "kanban.card.moved":
        position = {
            "type": "object",
            "required": ["column_id", "position"],
            "properties": {
                "column_id": {"type": ["string", "null"]},
                "position": {"type": ["integer", "null"]},
            },
        }
        payload_properties.update({"from": position, "to": position})
        payload_required.extend(["from", "to"])
    elif event_type == "kanban.card.status_changed":
        payload_properties.update(
            {
                "from_status": {"type": ["string", "null"]},
                "to_status": {"type": ["string", "null"]},
            }
        )
        payload_required.extend(["from_status", "to_status"])

    kanban_event = {
        "type": "object",
        "required": ["event_type", "timestamp", "payload"],
        "properties": {
            "event_type": {"const": event_type},
            "timestamp": {"type": "string"},
            "payload": {
                "type": "object",
                "required": payload_required,
                "properties": payload_properties,
            },
        },
    }
    manual_event = {
        "type": "object",
        "required": ["event_type", "timestamp", "payload"],
        "properties": {
            "event_type": {"const": event_type},
            "timestamp": {"type": "string"},
            "payload": {
                "type": "object",
                "required": ["event_type", "timestamp", "source"],
                "properties": {
                    "event_type": {"const": event_type},
                    "timestamp": {"type": "string"},
                    "source": {"const": "manual"},
                },
            },
        },
    }
    return {"oneOf": [kanban_event, manual_event]}


class WorkflowTrigger(BaseModel):
    """Workflow entrypoint definition with a canonical event-output contract."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["event"] = "event"
    event_type: str
    from_column: str | None = None
    to_column: str | None = None
    output_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def apply_canonical_output_schema(self) -> WorkflowTrigger:
        self.output_schema = workflow_trigger_output_schema(self.event_type)
        return self


class WorkflowBinding(BaseModel):
    """A constrained value source accepted by a prompt configuration."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["literal", "trigger", "previous_output", "reference"]
    value: Any | None = None
    path: str | None = None
    reference: str | None = None


class WorkflowRoutineReference(BaseModel):
    """Pinned immutable routine revision."""

    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int = Field(ge=1)


class WorkflowMcpToolReference(BaseModel):
    """Stable MCP tool selection."""

    model_config = ConfigDict(extra="forbid")

    server_id: str
    tool_name: str


class WorkflowSkillReference(BaseModel):
    """A mounted native or plugin skill."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["native", "plugin"]
    name: str
    segment: str | None = None


class WorkflowPromptReferences(BaseModel):
    """Stable source identifiers resolved by the backend at execution time."""

    model_config = ConfigDict(extra="forbid")

    prompt_path: str | None = None
    agent_id: str | None = None
    repository_id: str | None = None
    knowledge_base_id: str | None = None
    kanban_card_id: str | None = None
    inbox_message_id: str | None = None
    mcp_tool: WorkflowMcpToolReference | None = None
    routine: WorkflowRoutineReference | None = None
    skills: list[WorkflowSkillReference] = Field(default_factory=list, max_length=8)


class WorkflowNodePrompt(BaseModel):
    """A prompt step with an optional typed input and output contract."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["prompt"] = "prompt"
    title: str = "Prompt"
    prompt: str
    agent_name: str = WORKFLOW_DEFAULT_AGENT_NAME
    references: WorkflowPromptReferences = Field(default_factory=WorkflowPromptReferences)
    inputs: dict[str, WorkflowBinding] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class WorkflowNodeSavedPrompt(WorkflowNodePrompt):
    """A selected saved prompt with an editable execution instruction."""

    kind: Literal["saved_prompt"] = "saved_prompt"  # type: ignore[assignment]
    title: str = "Saved Prompt"
    prompt_path: str


class WorkflowNodeAgent(WorkflowNodePrompt):
    """An agent-backed prompt step."""

    kind: Literal["agent"] = "agent"  # type: ignore[assignment]
    title: str = "Call Agent"
    agent_id: str


class WorkflowNodeKnowledge(WorkflowNodePrompt):
    """A knowledge-base-backed prompt step."""

    kind: Literal["knowledge"] = "knowledge"  # type: ignore[assignment]
    title: str = "Search Knowledge"
    knowledge_base_id: str


class WorkflowNodeRepository(WorkflowNodePrompt):
    """A repository-backed prompt step."""

    kind: Literal["repository"] = "repository"  # type: ignore[assignment]
    title: str = "Repository Task"
    repository_id: str


class WorkflowNodeKanban(WorkflowNodePrompt):
    """A Kanban-card-backed prompt step."""

    kind: Literal["kanban"] = "kanban"  # type: ignore[assignment]
    title: str = "Kanban Task"
    kanban_card_id: str


class WorkflowNodeAskUser(WorkflowNodePrompt):
    """A prompt step that asks the user for a workflow decision or input."""

    kind: Literal["ask_user"] = "ask_user"  # type: ignore[assignment]
    title: str = "Ask User"


class WorkflowNodeInbox(WorkflowNodePrompt):
    """An inbox-message-backed prompt step."""

    kind: Literal["inbox"] = "inbox"  # type: ignore[assignment]
    title: str = "Use Inbox Message"
    inbox_message_id: str


class WorkflowNodeRoutine(WorkflowNodePrompt):
    """A pinned routine-backed prompt step."""

    kind: Literal["routine"] = "routine"  # type: ignore[assignment]
    title: str = "Run Routine"
    routine_id: str
    routine_revision: int = Field(ge=1)


class WorkflowNodeCondition(BaseModel):
    """Branch node with a typed boolean-routing result."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["condition"] = "condition"
    title: str = "Condition"
    expression: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "string", "enum": ["true", "false"]}
    )


class WorkflowNodeApproval(BaseModel):
    """Approval gate with a typed approval-routing result."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval"] = "approval"
    title: str = "Approval"
    message: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "string", "enum": ["approved", "rejected"]}
    )


class WorkflowNodeMcp(BaseModel):
    """MCP tool invocation node with typed bindings and output contract."""

    model_config = ConfigDict(extra="forbid")
    input_schema: dict[str, Any] | None = None

    kind: Literal["mcp"] = "mcp"
    title: str = "MCP Tool"
    server_id: str
    tool_name: str
    inputs: dict[str, WorkflowBinding] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None


WorkflowNode = Annotated[
    WorkflowNodePrompt
    | WorkflowNodeSavedPrompt
    | WorkflowNodeAgent
    | WorkflowNodeKnowledge
    | WorkflowNodeRepository
    | WorkflowNodeKanban
    | WorkflowNodeAskUser
    | WorkflowNodeInbox
    | WorkflowNodeRoutine
    | WorkflowNodeCondition
    | WorkflowNodeApproval
    | WorkflowNodeMcp,
    Field(discriminator="kind"),
]


def workflow_node_as_prompt(node: WorkflowNode) -> WorkflowNodePrompt:
    """Resolve a typed capability node into the shared prompt execution contract."""

    if not isinstance(node, WorkflowNodePrompt):
        raise TypeError(f"node '{node.kind}' does not execute as a prompt")

    reference_updates: dict[str, Any] = {}
    if isinstance(node, WorkflowNodeSavedPrompt):
        reference_updates["prompt_path"] = node.prompt_path
    elif isinstance(node, WorkflowNodeAgent):
        reference_updates["agent_id"] = node.agent_id
    elif isinstance(node, WorkflowNodeKnowledge):
        reference_updates["knowledge_base_id"] = node.knowledge_base_id
    elif isinstance(node, WorkflowNodeRepository):
        reference_updates["repository_id"] = node.repository_id
    elif isinstance(node, WorkflowNodeKanban):
        reference_updates["kanban_card_id"] = node.kanban_card_id
    elif isinstance(node, WorkflowNodeInbox):
        reference_updates["inbox_message_id"] = node.inbox_message_id
    elif isinstance(node, WorkflowNodeRoutine):
        reference_updates["routine"] = {
            "id": node.routine_id,
            "revision": node.routine_revision,
        }

    references = node.references.model_copy(update=reference_updates)
    return WorkflowNodePrompt(
        title=node.title,
        prompt=node.prompt,
        agent_name=node.agent_name,
        references=references,
        inputs=node.inputs,
        input_schema=node.input_schema,
        output_schema=node.output_schema,
    )


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
    prompt_node_id = f"prompt_{uuid.uuid4().hex[:8]}"
    return {
        "id": resolved_id,
        "title": title,
        "description": description,
        "enabled": True,
        "paused": False,
        "version": WORKFLOW_VERSION,
        "trigger": {"type": "event", "event_type": event_type},
        "graph": {
            "nodes": {
                prompt_node_id: {
                    "kind": "prompt",
                    "title": "Prompt",
                    "prompt": "Handle this workflow event.",
                    "agent_name": WORKFLOW_DEFAULT_AGENT_NAME,
                    "references": {},
                    "inputs": {},
                    "output_schema": None,
                }
            },
            "edges": [],
        },
        "ui": {
            "nodes": {
                prompt_node_id: {
                    "position": {"x": 456, "y": 160},
                    "size": {
                        "data": {"width": 340, "height": 160},
                        "settings": {"width": 340, "height": 280},
                    },
                }
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

    _validate_workflow_graph(definition)
    return definition.model_dump(mode="json", by_alias=True)


def ordered_workflow_node_ids(definition: WorkflowDefinition) -> list[str]:
    """Return the linear node order for an unbranched v1 workflow."""

    outgoing: dict[str, list[str]] = defaultdict(list)
    inbound: dict[str, int] = dict.fromkeys(definition.graph.nodes, 0)
    for edge in definition.graph.edges:
        outgoing[edge.from_.node].append(edge.to.node)
        inbound[edge.to.node] = inbound.get(edge.to.node, 0) + 1

    roots = [node_id for node_id, count in inbound.items() if count == 0]
    if len(roots) != 1:
        raise ValueError("workflow graph must contain exactly one root node in v1")
    ordered, seen, cursor = [], set(), roots[0]
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


def _validate_workflow_graph(definition: WorkflowDefinition) -> None:
    node_ids = set(definition.graph.nodes)
    if not node_ids:
        raise ValueError("workflow graph must contain at least one node")

    inbound: dict[str, int] = dict.fromkeys(node_ids, 0)
    outbound: dict[str, int] = dict.fromkeys(node_ids, 0)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in definition.graph.edges:
        source_id, target_id = edge.from_.node, edge.to.node
        if source_id not in node_ids:
            raise ValueError(f"edge references unknown source node '{source_id}'")
        if target_id not in node_ids:
            raise ValueError(f"edge references unknown target node '{target_id}'")
        outbound[source_id] += 1
        inbound[target_id] += 1
        adjacency[source_id].append(target_id)

    roots = [node_id for node_id, count in inbound.items() if count == 0]
    if len(roots) != 1:
        raise ValueError("workflow graph must contain exactly one root node in v1")
    _assert_acyclic(node_ids, adjacency)

    for node_id in roots:
        root = definition.graph.nodes[node_id]
        if hasattr(root, "input_schema"):
            root.input_schema = definition.trigger.output_schema
    for edge in definition.graph.edges:
        source = definition.graph.nodes[edge.from_.node]
        target = definition.graph.nodes[edge.to.node]
        if hasattr(target, "input_schema"):
            target.input_schema = getattr(source, "output_schema", None)

    for node_id, node in definition.graph.nodes.items():
        branching = isinstance(node, WorkflowNodeCondition | WorkflowNodeApproval)
        if inbound[node_id] > 1:
            raise ValueError(f"node '{node_id}' may have at most one inbound edge in v1")
        if not branching and outbound[node_id] > 1:
            raise ValueError(f"node '{node_id}' may have at most one outbound edge in v1")
        if branching:
            if outbound[node_id] > 2:
                raise ValueError(f"node '{node_id}' may have at most two outbound edges in v1")
            expected_ports = (
                {"true", "false"}
                if isinstance(node, WorkflowNodeCondition)
                else {"approved", "rejected"}
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
            if not node.agent_name.strip():
                raise ValueError(f"prompt node '{node_id}' is missing an agent_name")
            _validate_prompt_node(node_id, node)
        elif isinstance(node, WorkflowNodeCondition) and not node.expression.strip():
            raise ValueError(f"condition node '{node_id}' is missing an expression")
        elif isinstance(node, WorkflowNodeApproval) and not node.message.strip():
            raise ValueError(f"approval node '{node_id}' is missing a message")
        elif isinstance(node, WorkflowNodeMcp):
            if not node.server_id.strip():
                raise ValueError(f"mcp node '{node_id}' is missing a server_id")
            if not node.tool_name.strip():
                raise ValueError(f"mcp node '{node_id}' is missing a tool_name")
            _validate_mcp_node(node_id, node)


def _validate_prompt_node(node_id: str, node: WorkflowNodePrompt) -> None:
    """Reject configurations that could become backend queries or ambiguous paths."""

    for input_name, binding in node.inputs.items():
        if not input_name.strip():
            raise ValueError(f"prompt node '{node_id}' has an empty input name")
        if binding.source == "literal":
            if binding.path is not None or binding.reference is not None:
                raise ValueError(f"prompt node '{node_id}' literal binding is malformed")
        elif binding.source in {"trigger", "previous_output"}:
            if not binding.path or not binding.path.startswith("/"):
                raise ValueError(f"prompt node '{node_id}' binding path must start with '/'")
            if binding.value is not None or binding.reference is not None:
                raise ValueError(f"prompt node '{node_id}' binding is malformed")
        elif binding.source == "reference":
            if binding.reference not in {
                "prompt",
                "agent",
                "repository",
                "knowledge_base",
                "kanban_card",
                "inbox_message",
                "mcp_tool",
                "routine",
            }:
                raise ValueError(f"prompt node '{node_id}' has an unknown reference binding")
            if binding.value is not None or binding.path is not None:
                raise ValueError(f"prompt node '{node_id}' binding is malformed")
    for skill in node.references.skills:
        if skill.source == "plugin" and not skill.segment:
            raise ValueError(f"prompt node '{node_id}' plugin skill is missing a segment")
        if skill.source == "native" and skill.segment is not None:
            raise ValueError(f"prompt node '{node_id}' native skill cannot set a segment")


def _validate_mcp_node(node_id: str, node: WorkflowNodeMcp) -> None:
    """Reject malformed MCP tool bindings."""

    for input_name, binding in node.inputs.items():
        if not input_name.strip():
            raise ValueError(f"mcp node '{node_id}' has an empty input name")
        if binding.source == "literal":
            if binding.path is not None or binding.reference is not None:
                raise ValueError(f"mcp node '{node_id}' literal binding is malformed")
        elif binding.source in {"trigger", "previous_output"}:
            if not binding.path or not binding.path.startswith("/"):
                raise ValueError(f"mcp node '{node_id}' binding path must start with '/'")
            if binding.value is not None or binding.reference is not None:
                raise ValueError(f"mcp node '{node_id}' binding is malformed")
        elif binding.source == "reference" and (
            binding.value is not None or binding.path is not None
        ):
            raise ValueError(f"mcp node '{node_id}' reference binding is malformed")


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
