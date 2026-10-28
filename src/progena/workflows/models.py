"""Workflow node, graph, definition and run contracts. No runtime wiring."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.progena.documents.types import DocumentOperation
from src.progena.workflows.constants import (
    _JSON_OBJECT_SCHEMA,
    CRON_OUTPUT_SCHEMA,
    MAX_WORKFLOW_QUERY_LIMIT,
    WORKFLOW_VERSION,
    DocumentQueryOperator,
    WorkflowCronOverlap,
    WorkflowRunSource,
    WorkflowRunStatus,
)
from src.progena.workflows.schema_values import _unpack_input_schema, _unpack_output_schema
from src.progena.workflows.values import json_equal, path_exists, value_at_path


class DocumentQueryFilter(BaseModel):
    """One top-level document predicate and its derived input-port name."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    op: DocumentQueryOperator = "eq"


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


class WorkflowNodeManual(BaseModel):
    """Entrypoint started only by an explicit manual workflow invocation."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual"] = "manual"
    title: str = "Manual trigger"
    output_schema: dict[str, Any] = Field(default_factory=lambda: dict(_JSON_OBJECT_SCHEMA))


WorkflowTriggerNode = WorkflowNodeEvent | WorkflowNodeCron | WorkflowNodeManual


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
    """Prompt-backed agent step delegating to Progena."""

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
    # "human": only a person may decide (the UI); "any": Progena may too.
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
    | WorkflowNodeManual
    | WorkflowNodeData
    | WorkflowNodeDocumentRead
    | WorkflowNodeDocumentQuery
    | WorkflowNodeDocumentWrite
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
    tokens: dict[str, int] | None = None
    queued_at: str
    started_at: str | None = None
    updated_at: str
    completed_at: str | None = None


class WorkflowInvokeRequest(BaseModel):
    """Durable request for the backend executor to start one workflow run."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    run_id: str | None = None
    workflow_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    ancestry: list[str] = Field(default_factory=list)
    requested_at: str
