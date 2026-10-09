"""Model-backed prompt and classification node builders."""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from .graph import RoutineNode
from .messages import _message_with_usage, extract_json_object, normalize_message_content
from .state import (
    RoutineState,
    _context_data,
    _emit,
    _node_config,
    _require_no_file_source,
    _resolve_inputs,
    _resolve_response_schema,
)


def _combined_system_prompt(
    base_system_prompt: str | None, node_system_prompt: str | None
) -> str | None:
    parts = [p for p in (base_system_prompt, node_system_prompt) if p]
    return "\n\n".join(parts) if parts else None


def _build_prompt_node(
    node: RoutineNode,
    *,
    model: BaseChatModel,
    base_system_prompt: str | None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "prompt", config, "prompt_file")
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"prompt node {node.id}: config needs 'prompt_template'")
    node_system_prompt = config.get("system_prompt")
    response_schema = _resolve_response_schema(node.id, "prompt", config, defs)
    structured_model = (
        model.with_structured_output(
            {"title": "RoutineResponse", **response_schema},
            method="json_schema",
            strict=True,
            include_raw=True,
        )
        if response_schema
        else None
    )

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        data = {**state.get("data", {}), **_context_data(runtime)}
        try:
            rendered = template.format(**data)
        except KeyError as exc:
            raise KeyError(
                f"prompt node {node.id}: template references missing state key: {exc}"
            ) from exc

        messages: list[BaseMessage] = []
        system_prompt = _combined_system_prompt(base_system_prompt, node_system_prompt)
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=rendered))
        _emit(runtime, "routine.node.started", {"node": node.id, "kind": node.kind})

        if structured_model is not None:
            outcome = cast(dict[str, Any], await structured_model.ainvoke(messages))
            parsed = outcome["parsed"]
            if parsed is None:
                raise ValueError(
                    f"prompt node {node.id}: structured response failed schema "
                    f"validation: {outcome['parsing_error']}"
                )
            _emit(runtime, "routine.node.completed", {"node": node.id, "kind": node.kind})
            return {"messages": [outcome["raw"]], "data": {output_key: parsed}}

        response = await model.ainvoke(messages)
        text = normalize_message_content(response.content)
        _emit(runtime, "routine.node.completed", {"node": node.id, "kind": node.kind})

        result: dict[str, Any] = {output_key: text}
        parsed_text = extract_json_object(text)
        if parsed_text is not None:
            result[f"{output_key}_json"] = parsed_text
        return {
            "messages": [_message_with_usage(text, response)],
            "data": result,
        }

    return run


def _build_classify_node(
    node: RoutineNode, *, model: BaseChatModel, defs: dict[str, dict[str, Any]] | None = None
) -> Any:
    """Ask the model to judge a rubric against a required structured schema."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    rubric_prompt = config["rubric_prompt"]
    inputs_config = config.get("inputs") or {}
    response_schema = _resolve_response_schema(node.id, "classify", config, defs)
    if response_schema is None:
        raise ValueError(f"classify node {node.id}: config needs 'response_schema'")
    structured_model = model.with_structured_output(
        {"title": "RoutineResponse", **response_schema},
        method="json_schema",
        strict=True,
        include_raw=True,
    )

    async def run(state: RoutineState) -> dict[str, Any]:
        data = state.get("data", {})
        if inputs_config:
            data = {**data, **_resolve_inputs(node.id, inputs_config, data)}
        try:
            rendered = rubric_prompt.format(**data)
        except KeyError as exc:
            raise KeyError(
                f"classify node {node.id}: rubric_prompt references missing state key: {exc}"
            ) from exc

        outcome = cast(
            dict[str, Any], await structured_model.ainvoke([HumanMessage(content=rendered)])
        )
        parsed = outcome["parsed"]
        if parsed is None:
            raise ValueError(
                f"classify node {node.id}: structured response failed schema "
                f"validation: {outcome['parsing_error']}"
            )
        # `raw` carries usage_metadata -- threaded through `messages` like
        # every other model-calling node kind, purely so
        # _instrument_node_callable's uniform token-usage extraction (which
        # reads off `messages`, not any per-kind hook) sees it too.
        return {"messages": [outcome["raw"]], "data": {output_key: parsed}}

    return run
