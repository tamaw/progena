"""Workflow document identities, defaults and stored projections."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from src.progena.documents.kinds import (
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_INVOKE_REQUEST_KEY_PREFIX,
    WORKFLOW_QUEUE_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)
from src.progena.utils import utcnow_isoformat
from src.progena.workflows.constants import (
    _JSON_OBJECT_SCHEMA,
    WORKFLOW_DEFAULT_DOCUMENT_TYPE,
    WORKFLOW_VERSION,
)
from src.progena.workflows.models import (
    WorkflowDefinition,
    WorkflowInvokeRequest,
    WorkflowListItem,
    WorkflowNodeEvent,
    WorkflowNodeManual,
    WorkflowRun,
    WorkflowTriggerNode,
)
from src.progena.workflows.validation import validate_workflow_definition_payload


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
        (
            node.document_type
            if isinstance(node, WorkflowNodeEvent)
            else "manual"
            if isinstance(node, WorkflowNodeManual)
            else f"cron {node.expression}"
        )
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


def coerce_workflow_definition(payload: Mapping[str, Any]) -> WorkflowDefinition:
    """Validate and coerce a stored payload to ``WorkflowDefinition``."""

    return WorkflowDefinition.model_validate(validate_workflow_definition_payload(dict(payload)))


def coerce_workflow_run(payload: Mapping[str, Any]) -> WorkflowRun:
    """Validate and coerce a stored payload to ``WorkflowRun``."""

    return WorkflowRun.model_validate(dict(payload))


def parse_workflow_invoke_request_document(
    payload: Mapping[str, Any],
) -> WorkflowInvokeRequest:
    """Validate and coerce a stored payload to WorkflowInvokeRequest."""

    return WorkflowInvokeRequest.model_validate(dict(payload))
