"""Router graph — classifies and dispatches a request to one subagent."""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..client import BuilderClient
from .nodes.classify import create_classify_node
from .nodes.dispatch import (
    create_dispatch_build,
    create_dispatch_plan,
    create_dispatch_validate,
)
from .nodes.respond import create_respond_node
from .state import RouterState

logger = logging.getLogger(__name__)


def _route_selector(state: RouterState) -> str:
    return state.get("route", "build")


def create_router_graph(
    *,
    client: BuilderClient,
    plan_graph: CompiledStateGraph,
    build_graph: CompiledStateGraph,
    validate_graph: CompiledStateGraph,
) -> CompiledStateGraph:
    """Compile the top-level Builder router graph.

    Topology::

        classify -> (plan | build | validate) -> respond -> END
    """

    builder = StateGraph(RouterState)
    builder.add_node("classify", create_classify_node(client))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("plan", create_dispatch_plan(plan_graph))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("build", create_dispatch_build(build_graph))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("validate", create_dispatch_validate(validate_graph))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("respond", create_respond_node())  # type: ignore[call-overload,unused-ignore]

    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        _route_selector,
        {"plan": "plan", "build": "build", "validate": "validate"},
    )
    builder.add_edge("plan", "respond")
    builder.add_edge("build", "respond")
    builder.add_edge("validate", "respond")
    builder.add_edge("respond", END)

    graph = builder.compile()
    logger.info("Builder router graph compiled (classify -> {plan|build|validate} -> respond)")
    return graph
