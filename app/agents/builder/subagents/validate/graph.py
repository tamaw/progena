"""Validate subagent graph — single-node grader."""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...client import BuilderClient
from .nodes.score import create_score_node
from .state import ValidateState


def create_validate_graph(client: BuilderClient) -> CompiledStateGraph:
    builder = StateGraph(ValidateState)
    builder.add_node("score", create_score_node(client))  # type: ignore[call-overload,unused-ignore]
    builder.set_entry_point("score")
    builder.add_edge("score", END)
    graph = builder.compile()
    return graph
