"""Deterministic computation, human gates, and routine flow-control builders."""

from __future__ import annotations

from collections.abc import Callable
from math import prod
from typing import Any

from langgraph.errors import NodeError
from langgraph.runtime import Runtime
from langgraph.types import Command, Send, interrupt

from .expression import evaluate_expression
from .graph import RoutineEdge, RoutineNode
from .messages import normalize_message_content
from .state import (
    RoutineState,
    _context_data,
    _emit,
    _node_config,
    _require_no_file_source,
    resolve_data_path,
)


def _seed_node(input_fields: list[str] | None = None) -> Any:
    fields = input_fields or []

    async def seed(state: dict[str, Any], runtime: Runtime | None = None) -> dict[str, Any]:
        messages = state.get("messages") or []
        request = normalize_message_content(messages[-1].content) if messages else ""
        seeded: dict[str, Any] = {
            "request": request,
            **(state.get("data") or {}),
            **_context_data(runtime),
        }
        for field in fields:
            value = state.get(field)
            if value is not None:
                seeded[field] = value
        _emit(runtime, "routine.seed", {"request": request})
        return {"data": seeded}

    return seed


def _build_eval_node(node: RoutineNode) -> Any:
    """Deterministic expression evaluation; returns the raw result."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "eval", config, "expression_file")
    expression = config.get("expression")
    if not expression:
        raise ValueError(f"eval node {node.id}: config needs 'expression'")
    defaults = config.get("defaults") or {}

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        # `defaults` only ever fills a top-level name genuinely missing from
        # `data` -- a plain merge (not exception-driven fallback) is exactly
        # right here since dict union always lets the right-hand `data`
        # value win on a shared key, never masking something real.
        namespace = {**defaults, **data}
        result = evaluate_expression(expression, namespace)
        return {"data": {output_key: result}}

    return run


def _build_reduce_node(node: RoutineNode) -> Any:
    """Aggregate outputs recorded for each execution of a map/body node."""
    config = _node_config(node)
    operation = config["operation"]
    source_node = config["source_node"]
    output_key = node.output_key or node.id

    async def run(state: RoutineState) -> dict[str, Any]:
        values = list((state.get("node_outputs") or {}).get(source_node, []))
        if operation == "collect":
            result: Any = values
        elif operation == "count":
            result = len(values)
        elif operation == "sum":
            result = sum(values)
        elif operation == "product":
            result = prod(values)
        elif operation == "min":
            result = min(values) if values else None
        elif operation == "max":
            result = max(values) if values else None
        else:  # guarded by ReduceNodeConfig; keeps direct compiler use explicit
            raise ValueError(f"reduce node {node.id}: unsupported operation {operation!r}")
        return {"data": {output_key: result}}

    return run


def _build_condition_node(node: RoutineNode) -> Any:
    """Deterministic expression evaluation, coerced to a boolean gate."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "condition", config, "expression_file")
    expression = config.get("expression")
    if not expression:
        raise ValueError(f"condition node {node.id}: config needs 'expression'")

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        result = bool(evaluate_expression(expression, data))
        return {"data": {output_key: result}}

    return run


def _build_switch_node(node: RoutineNode) -> Any:
    """Deterministic expression evaluation; the raw result becomes the value
    an author-defined branch set routes on (see `_build_split_router`'s
    sibling `_build_router`, unchanged for `switch`). Unlike `condition`,
    the result is not coerced to a bool."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "switch", config, "expression_file")
    expression = config.get("expression")
    if not expression:
        raise ValueError(f"switch node {node.id}: config needs 'expression'")

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        result = evaluate_expression(expression, data)
        return {"data": {output_key: result}}

    return run


def _build_split_node(node: RoutineNode) -> Any:
    """Structural parallel gateway: no computation, exactly like `noop` --
    all the real behavior lives in `_build_split_router` on its outgoing
    edge, which takes every branch unconditionally rather than evaluating
    anything this node's own config could hold."""

    async def run(state: RoutineState) -> dict[str, Any]:
        return {}

    return run


def _build_noop_node(node: RoutineNode) -> Any:
    async def run(state: RoutineState) -> dict[str, Any]:
        return {}

    return run


def _build_human_node(node: RoutineNode) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id
    message = config["message"]
    allowed_responses = config.get("allowed_responses")

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        answer = interrupt(
            {
                "node": node.id,
                "message": message,
                "approver": config.get("approver", "any"),
                **(
                    {"allowed_responses": allowed_responses}
                    if allowed_responses is not None
                    else {}
                ),
            }
        )
        if allowed_responses is not None and answer not in allowed_responses:
            raise ValueError(
                f"human node {node.id}: response must be one of {allowed_responses!r}, got {answer!r}"
            )
        _emit(runtime, "routine.human.resumed", {"node": node.id})
        return {"data": {output_key: answer}}

    return run


def _foreach_items(node: RoutineNode, state: RoutineState) -> list[Any]:
    config = _node_config(node)
    items_from = config["items_from"]
    items = resolve_data_path(state.get("data", {}), items_from)
    if not isinstance(items, list):
        raise ValueError(f"foreach node {node.id}: items_from must resolve to a list")
    max_items = config.get("max_items")
    return items[:max_items] if max_items is not None else items


def _build_send_router(
    node: RoutineNode, target: str, empty_target: str | None = None
) -> Callable[[RoutineState], list[Send] | str]:
    config = _node_config(node)
    item_key = config["item_key"]

    def route(state: RoutineState) -> list[Send] | str:
        items = _foreach_items(node, state)
        data = state.get("data", {})
        if not items and empty_target:
            return empty_target
        return [
            Send(
                target, {"messages": state.get("messages") or [], "data": {**data, item_key: item}}
            )
            for item in items
        ]

    return route


def _branch_key(value: Any) -> str:
    """Branch keys are authored in a JSON document, so a boolean routes on
    JSON's ``true``/``false`` rather than Python's ``str(True)`` -> ``"True"``.
    A ``condition`` node's gate is always a bool, so this is the casing its
    two mandatory branches use (see ``validate_routine_graph``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _build_router(edge: RoutineEdge) -> Callable[[RoutineState], str]:
    branches = edge.branches or {}

    def route(state: RoutineState) -> str:
        data = state.get("data", {})
        try:
            value = resolve_data_path(data, edge.state_key or "")
        except KeyError as exc:
            raise ValueError(f"router from {edge.from_}: {exc}") from exc

        key = _branch_key(value)
        if key in branches:
            return branches[key]
        if edge.default is not None:
            return edge.default
        raise ValueError(
            f"router from {edge.from_}: no branch for state '{edge.state_key}'={key!r} and no default"
        )

    return route


def _build_split_router(edge: RoutineEdge) -> Callable[[RoutineState], list[str]]:
    """`split`'s router: no expression, no picking -- every branch (and the
    default, if wired) runs, always. `edge.state_key` is present only to
    satisfy the router edge shape and is never read; a `split` node has
    nothing to evaluate it against."""
    targets = list((edge.branches or {}).values())
    if edge.default is not None:
        targets.append(edge.default)

    def route(state: RoutineState) -> list[str]:
        return targets

    return route


def _build_retry_exhausted_handler(
    node_id: str, route_to: str
) -> Callable[[RoutineState, NodeError], Command]:
    """Turn an exhausted LangGraph retry into an explicit recovery transition."""

    def recover(state: RoutineState, error: NodeError) -> Command:
        data = state.get("data", {})
        errors = dict(data.get("errors") or {})
        errors[node_id] = {
            "type": type(error.error).__name__,
            "message": str(error.error),
        }
        return Command(update={"data": {"errors": errors}}, goto=route_to)

    return recover


def _build_loop_guard(
    edge: RoutineEdge, terminal_target: str
) -> Callable[[RoutineState], str | list[str]]:
    guard = edge.loop_guard
    if guard is None:
        raise ValueError(f"edge from {edge.from_} has no loop_guard")
    targets = edge.target_ids
    false_target = guard.on_false or terminal_target

    def route(state: RoutineState) -> str | list[str]:
        allowed = evaluate_expression(guard.expression, state.get("data", {}))
        if not isinstance(allowed, bool):
            raise ValueError(f"loop_guard from {edge.from_} must evaluate to a boolean")
        if not allowed:
            return false_target
        return targets[0] if len(targets) == 1 else targets

    return route


def _build_output_projector(output_fields: list[str]) -> Any:
    """Copies each declared output_schema field's value out of ``data`` (via
    the same dotted-path resolution routerEdge.state_key uses) into its own
    top-level channel, which is what makes output_schema's projection real:
    invoke() returns exactly {messages, **declared fields}. A field absent
    from data at this point is silently omitted rather than erroring -- not
    every terminal path necessarily produces every declared output field."""

    async def project(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        result: dict[str, Any] = {}
        for field in output_fields:
            try:
                result[field] = resolve_data_path(data, field)
            except KeyError:
                continue
        return result

    return project
