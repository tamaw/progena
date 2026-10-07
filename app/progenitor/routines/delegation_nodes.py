"""Awaited object-agent, A2A peer, and nested-routine delegation builders.

Object-agent invocation is kept here as the seam for a future background adapter.
Current nodes await their results; execution selection and task lifecycle are not
part of this module.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

from ..structured import validate_against_schema
from .control_nodes import _foreach_items
from .graph import RoutineNode
from .messages import _message_with_usage, extract_json_object, normalize_message_content
from .state import (
    RoutineState,
    _emit,
    _node_config,
    _require_no_file_source,
    _resolve_inputs,
    _resolve_response_schema,
)


def _build_object_agent_node(
    node: RoutineNode,
    *,
    object_agent_registry: dict[str, Any],
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Conversational delegation to a local object agent, keyed to stay on the
    same checkpointer thread across repeat invocations of this node within one
    routine run (``continue_thread``, default on) rather than starting a fresh
    conversation each time."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "agent", config, "prompt_file")
    agent_name = config["agent_name"]
    template = config.get("prompt_template")
    inputs_config = config.get("inputs") or {}
    if not template and not inputs_config:
        raise ValueError(f"agent node {node.id}: config needs 'prompt_template' or 'inputs'")
    response_schema = _resolve_response_schema(node.id, "agent", config, defs)
    continue_thread = config.get("continue_thread", True)

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        if agent_name not in object_agent_registry:
            raise KeyError(f"node {node.id}: unknown agent '{agent_name}'")
        data = state.get("data", {})
        if inputs_config:
            namespace = _resolve_inputs(node.id, inputs_config, data)
            rendered = json.dumps(namespace)
        else:
            assert template is not None  # guaranteed by the guard above
            try:
                rendered = template.format(**data)
            except KeyError as exc:
                raise KeyError(
                    f"agent node {node.id}: template references missing state key: {exc}"
                ) from exc

        target = object_agent_registry[agent_name]
        invoke_kwargs: dict[str, Any] = {}
        if continue_thread:
            context = getattr(runtime, "context", None) if runtime is not None else None
            correlation = (context or {}).get("request_id") or "routine"
            invoke_kwargs["config"] = {
                "configurable": {"thread_id": f"routine-agent:{correlation}:{node.id}"}
            }
        result = await target.ainvoke(
            {"messages": [HumanMessage(content=rendered)]}, **invoke_kwargs
        )
        target_messages = result.get("messages") or []
        target_message = target_messages[-1] if target_messages else None
        text = normalize_message_content(target_message.content) if target_message else ""

        if response_schema is not None:
            parsed = extract_json_object(text)
            if parsed is None:
                raise ValueError(f"agent node {node.id}: structured response was not a JSON object")
            validation_error = validate_against_schema(parsed, response_schema)
            if validation_error is not None:
                raise ValueError(
                    f"agent node {node.id}: structured response failed schema validation: "
                    f"{validation_error}"
                )
            return {
                "messages": [_message_with_usage(text, target_message)],
                "data": {output_key: parsed},
            }

        output: dict[str, Any] = {output_key: text}
        parsed = extract_json_object(text)
        if parsed is not None:
            output[f"{output_key}_json"] = parsed

        return {"messages": [_message_with_usage(text, target_message)], "data": output}

    return run


def _build_a2a_node(node: RoutineNode, *, tool_registry: Any) -> Any:
    """One-shot delegation to a registered Agent2Agent-protocol peer, resolved
    the same way the ``@agent_name request`` direct-call chat shortcut does
    (see ``DirectA2AFastPathMiddleware``)."""
    from ..a2a import A2AClient, extract_message_text, summarize_task_payload

    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "a2a", config, "prompt_file")
    agent_name = config["agent_name"]
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"a2a node {node.id}: config needs 'prompt_template'")

    def _extract_reply_text(payload: Any) -> str:
        if isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, dict):
                text = extract_message_text(status.get("message"))
                if text:
                    return text
            text = summarize_task_payload(payload)
            if text:
                return text
        return f"{agent_name} completed the request."

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        data = state.get("data", {})
        try:
            rendered = template.format(**data)
        except KeyError as exc:
            raise KeyError(
                f"a2a node {node.id}: template references missing state key: {exc}"
            ) from exc

        find_by_name = getattr(tool_registry, "find_by_name", None) if tool_registry else None
        cfg = find_by_name(agent_name) if callable(find_by_name) else None
        if not isinstance(cfg, dict):
            raise KeyError(f"node {node.id}: unknown A2A agent '{agent_name}'")
        if (
            str(cfg.get("transport") or "").lower() != "http"
            or not str(cfg.get("url") or "").strip()
        ):
            raise ValueError(
                f"a2a node {node.id}: '{agent_name}' is not a reachable HTTP A2A agent"
            )

        context = getattr(runtime, "context", None) if runtime is not None else None
        thread_id = (context or {}).get("request_id")
        response = await A2AClient().call_agent(
            cfg,
            rendered,
            blocking=True,
            context={"caller": "routine", "routing_mode": "routine_node", "thread_id": thread_id},
        )
        if not response.get("success"):
            raise ValueError(f"a2a node {node.id}: {response.get('error') or 'unknown error'}")

        text = _extract_reply_text(response.get("result"))
        output: dict[str, Any] = {output_key: text}
        parsed = extract_json_object(text)
        if parsed is not None:
            output[f"{output_key}_json"] = parsed
        return {"messages": [AIMessage(content=text)], "data": output}

    return run


def _build_foreach_node(node: RoutineNode, *, object_agent_registry: dict[str, Any]) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id

    if config["mode"] == "send":

        async def run_send(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
            items = _foreach_items(node, state)
            _emit(runtime, "routine.foreach.expanded", {"node": node.id, "count": len(items)})
            return {"data": {output_key: items}}

        return run_send

    agent_name = config["agent_name"]
    template = config["prompt_template"]
    item_key = config["item_key"]
    max_concurrency = config.get("max_concurrency") or 4

    async def run_agent(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        target = object_agent_registry.get(agent_name)
        if target is None:
            raise KeyError(f"foreach node {node.id}: unknown agent '{agent_name}'")
        items = _foreach_items(node, state)
        data = state.get("data", {})
        semaphore = asyncio.Semaphore(max_concurrency)

        async def invoke(item: Any) -> str:
            try:
                rendered = template.format(**data, **{item_key: item})
            except KeyError as exc:
                raise KeyError(
                    f"foreach node {node.id}: template references missing state key: {exc}"
                ) from exc
            async with semaphore:
                result = await target.ainvoke({"messages": [HumanMessage(content=rendered)]})
            messages = result.get("messages") or []
            return normalize_message_content(messages[-1].content) if messages else ""

        results = await asyncio.gather(*(invoke(item) for item in items))
        _emit(runtime, "routine.foreach.completed", {"node": node.id, "count": len(results)})
        return {"data": {output_key: results}}

    return run_agent


def _build_routine_node(
    node: RoutineNode,
    *,
    routine_registry: dict[str, Any],
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id
    name = config["routine_name"]
    input_map = config.get("input_map") or {}
    output_map = config.get("output_map") or {}

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        parent_data = state.get("data", {})
        child_data = (
            _resolve_inputs(node.id, input_map, parent_data) if input_map else dict(parent_data)
        )
        context = getattr(runtime, "context", None) if runtime is not None else None
        child_state = {
            "messages": state.get("messages") or [],
            "data": child_data,
            "_routine_parent": {
                "thread_id": (context or {}).get("request_id"),
                "node_id": node.id,
            },
        }
        if routine_runner is not None:
            result = await routine_runner(name, child_state, runtime)
        else:
            target = routine_registry.get(name)
            if target is None:
                raise KeyError(f"node {node.id}: unknown routine '{name}'")
            if runtime is None:
                result = await target.ainvoke(child_state)
            else:
                result = await target.ainvoke(child_state, context=runtime.context)
        messages = result.get("messages") or []
        text = normalize_message_content(messages[-1].content) if messages else ""
        result_data = result.get("data") or {}
        mapped_output = _resolve_inputs(node.id, output_map, result_data) if output_map else {}
        update = {
            "messages": [AIMessage(content=text)],
            "data": {**mapped_output, output_key: text},
        }
        if result.get("_routine_invocation"):
            update["_routine_invocation"] = result["_routine_invocation"]
        return update

    return run
