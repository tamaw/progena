"""Workflow graph ordering and cycle detection."""

from __future__ import annotations

from collections import defaultdict

from src.progena.workflows.models import WorkflowDefinition, WorkflowTriggerNode


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
