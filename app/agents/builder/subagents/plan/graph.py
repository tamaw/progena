"""Plan subagent graph — single-node graph that shapes a request into a plan."""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...client import BuilderClient
from .nodes.shape_request import create_shape_request_node
from .state import PlanState


def create_plan_graph(client: BuilderClient) -> CompiledStateGraph:
    builder = StateGraph(PlanState)
    builder.add_node("shape_request", create_shape_request_node(client))  # type: ignore[call-overload,unused-ignore]
    builder.set_entry_point("shape_request")
    builder.add_edge("shape_request", END)
    graph = builder.compile()
    return graph
