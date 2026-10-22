"""Workflow document validation and graph compatibility rules."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from app.progenitor.cron import cron_error
from app.progenitor.documents.catalog import load_schema
from app.progenitor.documents.types import DocumentType
from app.progenitor.llm.structured import validate_against_schema
from app.progenitor.workflows.constants import RoutineLoader
from app.progenitor.workflows.derivation import derive_workflow_port_schemas
from app.progenitor.workflows.graph import _assert_acyclic
from app.progenitor.workflows.models import (
    WorkflowDefinition,
    WorkflowNodeAgent,
    WorkflowNodeApproval,
    WorkflowNodeCondition,
    WorkflowNodeCron,
    WorkflowNodeData,
    WorkflowNodeEvent,
    WorkflowNodeExtract,
    WorkflowNodeFormat,
    WorkflowTriggerNode,
)
from app.progenitor.workflows.ports import workflow_node_input_ports, workflow_node_output_ports
from app.progenitor.workflows.schema_values import schema_at_path


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
                f"edge source '{edge.from_.node}' has no output port '{edge.from_.port}'; "
                f"{source.kind} node '{edge.from_.node}' outputs: {_describe_ports(output_ports)}"
            )
        input_ports = workflow_node_input_ports(target)
        if edge.to.port not in input_ports:
            raise ValueError(
                f"edge target '{edge.to.node}' has no input port '{edge.to.port}'; "
                f"{target.kind} node '{edge.to.node}' inputs: {_describe_ports(input_ports)}"
            )
        target_key = (edge.to.node, edge.to.port)
        if target_key in seen_targets:
            raise ValueError(
                f"input port '{edge.to.port}' on node '{edge.to.node}' has more than one edge"
            )
        seen_targets.add(target_key)
        if not _schema_types_compatible(output_ports[edge.from_.port], input_ports[edge.to.port]):
            raise ValueError(
                f"edge from '{edge.from_.node}.{edge.from_.port}' to "
                f"'{edge.to.node}.{edge.to.port}' has incompatible types: "
                f"{_describe_schema(output_ports[edge.from_.port])} -> "
                f"{_describe_schema(input_ports[edge.to.port])}"
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
            raise ValueError(
                f"executable node '{node_id}' has no inbound connection; wire an edge into one "
                f"of its inputs: {_describe_ports(workflow_node_input_ports(node))}"
            )
        input_schema = getattr(node, "input_schema", None) or {}
        properties = input_schema.get("properties") or {}
        for port in input_schema.get("required") or []:
            if (node_id, port) not in seen_targets:
                raise ValueError(
                    f"required input '{port}' on node '{node_id}' has no edge; wire an output "
                    f"of type {_describe_schema(properties.get(port) or {})} into it"
                )

    _assert_acyclic(set(nodes), adjacency)


def _describe_schema(schema: Mapping[str, Any]) -> str:
    """Short human/LLM-readable type label for one port schema in save errors."""

    if schema.get("title"):
        return str(schema["title"])
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(str(item) for item in kind)
    if kind == "array" and isinstance(schema.get("items"), Mapping):
        return f"array of {_describe_schema(schema['items'])}"
    return str(kind) if kind else "any"


def _describe_ports(ports: Mapping[str, Mapping[str, Any]]) -> str:
    """``name (type), ...`` listing of a node's ports for save errors."""

    if not ports:
        return "(none)"
    return ", ".join(f"{name} ({_describe_schema(schema)})" for name, schema in ports.items())
