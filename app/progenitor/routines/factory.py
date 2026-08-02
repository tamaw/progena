"""Compiles a validated :class:`RoutineGraph` into a runnable LangGraph ``StateGraph``.

Scope note: this pass implements the ``prompt``/``code``/``eval``/``subagent``/``agent``/
``noop`` node kinds and ``state_equals`` routing only. Retry policies, loop guards, and the
``human``/``foreach`` node kinds are deferred follow-ups (see the plan this shipped under).
Nothing in the live progenitor runtime invokes a compiled routine yet — ``compile_routine_graph``
produces a correct, independently testable runnable via ``.ainvoke(...)``, wiring it into
DeepAgent subagent delegation is a separate task.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from .expression import evaluate_expression
from .graph import RoutineEdge, RoutineGraph, RoutineNode

_SEED_NODE_ID = "__seed__"

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _merge_data(existing: dict[str, Any] | None, update: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing) if existing else {}
    if update:
        merged.update(update)
    return merged


class RoutineState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    data: Annotated[dict[str, Any], _merge_data]


# Documents the node-callable shape; builder functions below are annotated -> Any
# rather than -> NodeCallable, mirroring heartbeat_graph.py's _guard_maintenance_node
# convention, since StateGraph.add_node's overloads don't resolve against a plain
# Callable alias.
NodeCallable = Callable[[RoutineState], Awaitable[dict[str, Any]]]


def normalize_message_content(content: Any) -> str:
    """Reduce LangChain message content (str, or Responses-API block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON-object extraction: fenced block, then a balanced-brace scan."""
    candidates: list[str] = []
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    brace_substring = _balanced_brace_substring(text)
    if brace_substring:
        candidates.append(brace_substring)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _balanced_brace_substring(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def resolve_data_path(data: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path (e.g. ``parse_json.host``) against ``data``."""
    value: Any = data
    consumed: list[str] = []
    for segment in path.split("."):
        consumed.append(segment)
        if not isinstance(value, dict) or segment not in value:
            raise KeyError(f"missing state path: {'.'.join(consumed)} (from '{path}')")
        value = value[segment]
    return value


def _resolve_inputs(node_id: str, inputs_config: dict[str, str], data: dict[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    for local_name, path in inputs_config.items():
        try:
            namespace[local_name] = resolve_data_path(data, path)
        except KeyError as exc:
            raise KeyError(f"node {node_id}: {exc}") from exc
    return namespace


def _require_no_file_source(node_id: str, kind: str, config: dict[str, Any], file_key: str) -> None:
    if config.get(file_key):
        raise NotImplementedError(
            f"{kind} node {node_id}: '{file_key}' requires a filesystem agent directory, "
            "not supported by this Postgres-backed compiler"
        )


def _combined_system_prompt(base_system_prompt: str | None, node_system_prompt: str | None) -> str | None:
    parts = [p for p in (base_system_prompt, node_system_prompt) if p]
    return "\n\n".join(parts) if parts else None


def _seed_node() -> Any:
    async def seed(state: RoutineState) -> dict[str, Any]:
        messages = state.get("messages") or []
        request = normalize_message_content(messages[-1].content) if messages else ""
        return {"data": {"request": request}}

    return seed


def _build_prompt_node(node: RoutineNode, *, model: BaseChatModel, base_system_prompt: str | None) -> Any:
    config = node.config
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "prompt", config, "prompt_file")
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"prompt node {node.id}: config needs 'prompt_template'")
    node_system_prompt = config.get("system_prompt")

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        try:
            rendered = template.format(**data)
        except KeyError as exc:
            raise KeyError(f"prompt node {node.id}: template references missing state key: {exc}") from exc

        messages: list[BaseMessage] = []
        system_prompt = _combined_system_prompt(base_system_prompt, node_system_prompt)
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=rendered))

        response = await model.ainvoke(messages)
        text = normalize_message_content(response.content)

        result: dict[str, Any] = {output_key: text}
        parsed = extract_json_object(text)
        if parsed is not None:
            result[f"{output_key}_json"] = parsed

        return {"messages": [AIMessage(content=text)], "data": result}

    return run


def _build_code_node(node: RoutineNode) -> Any:
    config = node.config
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "code", config, "expression_file")
    expression = config.get("expression")
    if not expression:
        raise ValueError(f"code node {node.id}: config needs 'expression'")
    inputs_config = config.get("inputs") or {}

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        namespace = _resolve_inputs(node.id, inputs_config, data)
        result = evaluate_expression(expression, namespace)
        return {"data": {output_key: result}}

    return run


def _build_eval_node(node: RoutineNode, *, model: BaseChatModel) -> Any:
    config = node.config
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "eval", config, "expression_file")
    expression = config.get("expression")
    inputs_config = config.get("inputs") or {}
    rubric_prompt = config.get("rubric_prompt")

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        if expression:
            namespace = _resolve_inputs(node.id, inputs_config, data)
            result = bool(evaluate_expression(expression, namespace))
            return {"data": {output_key: result}}

        if not rubric_prompt:
            raise ValueError(f"eval node {node.id}: config needs 'expression' or 'rubric_prompt'")
        try:
            rendered = rubric_prompt.format(**data)
        except KeyError as exc:
            raise KeyError(f"eval node {node.id}: rubric_prompt references missing state key: {exc}") from exc
        response = await model.ainvoke([HumanMessage(content=rendered)])
        text = normalize_message_content(response.content).strip()
        result = text.lower().startswith("true")
        return {"data": {output_key: result}}

    return run


def _build_subagent_node(node: RoutineNode, *, subagent_registry: dict[str, Any]) -> Any:
    config = node.config
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "subagent", config, "prompt_file")
    subagent_name = config["subagent_name"]
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"subagent node {node.id}: config needs 'prompt_template'")

    async def run(state: RoutineState) -> dict[str, Any]:
        if subagent_name not in subagent_registry:
            raise KeyError(f"node {node.id}: unknown subagent '{subagent_name}'")
        data = state.get("data", {})
        try:
            rendered = template.format(**data)
        except KeyError as exc:
            raise KeyError(f"subagent node {node.id}: template references missing state key: {exc}") from exc

        target = subagent_registry[subagent_name]
        result = await target.ainvoke({"messages": [HumanMessage(content=rendered)]})
        target_messages = result.get("messages") or []
        text = normalize_message_content(target_messages[-1].content) if target_messages else ""

        return {"messages": [AIMessage(content=text)], "data": {output_key: text}}

    return run


def _build_agent_node(node: RoutineNode, *, agent_registry: dict[str, Any]) -> Any:
    config = node.config
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "agent", config, "prompt_file")
    agent_name = config["agent_name"]
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"agent node {node.id}: config needs 'prompt_template'")

    async def run(state: RoutineState) -> dict[str, Any]:
        if agent_name not in agent_registry:
            raise KeyError(f"node {node.id}: unknown agent '{agent_name}'")
        data = state.get("data", {})
        try:
            rendered = template.format(**data)
        except KeyError as exc:
            raise KeyError(f"agent node {node.id}: template references missing state key: {exc}") from exc

        target = agent_registry[agent_name]
        result = await target.ainvoke({"messages": [HumanMessage(content=rendered)]})
        target_messages = result.get("messages") or []
        text = normalize_message_content(target_messages[-1].content) if target_messages else ""

        output: dict[str, Any] = {output_key: text}
        parsed = extract_json_object(text)
        if parsed is not None:
            output[f"{output_key}_json"] = parsed

        return {"messages": [AIMessage(content=text)], "data": output}

    return run


def _build_noop_node(node: RoutineNode) -> Any:
    async def run(state: RoutineState) -> dict[str, Any]:
        return {}

    return run


def _build_node_callable(
    node: RoutineNode,
    *,
    model: BaseChatModel,
    base_system_prompt: str | None,
    subagent_registry: dict[str, Any],
    agent_registry: dict[str, Any],
) -> Any:
    if node.kind == "prompt":
        return _build_prompt_node(node, model=model, base_system_prompt=base_system_prompt)
    if node.kind == "code":
        return _build_code_node(node)
    if node.kind == "eval":
        return _build_eval_node(node, model=model)
    if node.kind == "subagent":
        return _build_subagent_node(node, subagent_registry=subagent_registry)
    if node.kind == "agent":
        return _build_agent_node(node, agent_registry=agent_registry)
    if node.kind == "noop":
        return _build_noop_node(node)
    raise ValueError(f"unknown node kind: {node.kind}")


def _build_router(edge: RoutineEdge) -> Callable[[RoutineState], str]:
    branches = edge.branches or {}

    def route(state: RoutineState) -> str:
        data = state.get("data", {})
        try:
            value = resolve_data_path(data, edge.state_key or "")
        except KeyError as exc:
            raise ValueError(f"router from {edge.from_}: {exc}") from exc

        key = str(value)
        if key in branches:
            return branches[key]
        if edge.default is not None:
            return edge.default
        raise ValueError(
            f"router from {edge.from_}: no branch for state '{edge.state_key}'={key!r} and no default"
        )

    return route


def compile_routine_graph(
    graph: RoutineGraph,
    *,
    model: BaseChatModel,
    subagent_registry: dict[str, Any] | None = None,
    agent_registry: dict[str, Any] | None = None,
    base_system_prompt: str | None = None,
):
    """Compile a validated ``RoutineGraph`` into a runnable ``StateGraph``.

    ``subagent_registry``/``agent_registry`` default to empty dicts (no real subagent/agent
    wiring in this pass — callers pass fakes in tests, or a real registry once that follow-up
    lands). Missing registry lookups raise ``KeyError`` at node-execution time, not compile
    time, mirroring the source spec this was adapted from.
    """
    subagent_registry = subagent_registry or {}
    agent_registry = agent_registry or {}

    node_ids = {node.id for node in graph.nodes}
    if graph.entry not in node_ids:
        raise ValueError(f"entry references unknown node: {graph.entry}")

    builder = StateGraph(RoutineState)
    builder.add_node(_SEED_NODE_ID, _seed_node())
    builder.add_edge(START, _SEED_NODE_ID)
    builder.add_edge(_SEED_NODE_ID, graph.entry)

    for node in graph.nodes:
        builder.add_node(
            node.id,
            _build_node_callable(
                node,
                model=model,
                base_system_prompt=base_system_prompt,
                subagent_registry=subagent_registry,
                agent_registry=agent_registry,
            ),
        )

    outgoing: dict[str, list[RoutineEdge]] = defaultdict(list)
    for edge in graph.edges:
        outgoing[edge.from_].append(edge)

    for node in graph.nodes:
        edges = outgoing.get(node.id, [])
        if not edges:
            builder.add_edge(node.id, END)
            continue
        router_edges = [edge for edge in edges if edge.is_router]
        if router_edges:
            builder.add_conditional_edges(node.id, _build_router(router_edges[0]))
        else:
            for edge in edges:
                if edge.to is None:
                    raise ValueError(f"edge from {edge.from_} has no target")
                builder.add_edge(node.id, edge.to)

    return builder.compile()
