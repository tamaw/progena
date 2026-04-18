"""Build subagent graph — setup -> execute -> validate -> {teardown|teardown_failed}."""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...client import BuilderClient
from .nodes.execute import create_execute_node
from .nodes.setup import create_setup_node
from .nodes.teardown import create_teardown_failed_node, create_teardown_node
from .nodes.validate import create_validate_node
from .state import BuildState


def _route_after_validate(state: BuildState) -> str:
    """Route based on the validate subagent's verdict.

    ``fail`` → cleanup-only ``teardown_failed`` (no push, no PR).
    Anything else (``pass`` / ``pass_with_concerns`` / missing report) → the
    normal teardown that may push and open a PR.
    """
    report = state.get("validation_report") or {}
    verdict = report.get("verdict")
    return "teardown_failed" if verdict == "fail" else "teardown"


def create_build_graph(
    *,
    deep_agent: CompiledStateGraph,
    validate_graph: CompiledStateGraph,
    client: BuilderClient,
) -> CompiledStateGraph:
    """Compile the build subagent graph.

    The build subagent owns the heavy implementation flow:
    ``setup -> execute -> validate -> {teardown | teardown_failed}``.

    The validate subagent runs against the structured execution evidence
    (tool calls + tool results) and its verdict gates the teardown branch:
    a ``fail`` verdict aborts the build before any push or PR is created.
    """

    builder = StateGraph(BuildState)
    builder.add_node("setup", create_setup_node(deep_agent, client))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("execute", create_execute_node(deep_agent))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("validate", create_validate_node(validate_graph))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("teardown", create_teardown_node(deep_agent))  # type: ignore[call-overload,unused-ignore]
    builder.add_node("teardown_failed", create_teardown_failed_node(deep_agent))  # type: ignore[call-overload,unused-ignore]
    builder.set_entry_point("setup")
    builder.add_edge("setup", "execute")
    builder.add_edge("execute", "validate")
    builder.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"teardown": "teardown", "teardown_failed": "teardown_failed"},
    )
    builder.add_edge("teardown", END)
    builder.add_edge("teardown_failed", END)
    return builder.compile()
