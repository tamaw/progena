"""Model-backed prompt and classification node builders."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from .graph import RoutineNode
from .messages import _message_with_usage, extract_json_object, normalize_message_content
from .node_output import (
    NodeOutputValidationError,
    OutputFeedback,
    OutputSchema,
    build_output_feedback,
    get_output_feedback,
    output_task_id,
)
from .state import (
    RoutineState,
    _context_data,
    _emit,
    _node_config,
    _require_no_file_source,
    _resolve_inputs,
)


def _native_attempt(runtime: Runtime | None, record: dict[str, Any]) -> int:
    execution_info = getattr(runtime, "execution_info", None)
    attempt = getattr(execution_info, "node_attempt", None)
    return attempt if isinstance(attempt, int) else len(record["attempts"]) + 1


def _retry_messages(
    node_id: str, runtime: Runtime | None, schema: OutputSchema
) -> tuple[OutputFeedback | None, list[BaseMessage]]:
    feedback = get_output_feedback(node_id, runtime)
    if feedback is None:
        return None, []
    # The rejected model output remains ordinary assistant content. It is never
    # quoted into a system instruction, and reconstructing it strips any
    # provider tool-call metadata that would otherwise be orphaned on retry.
    return feedback, [
        AIMessage(content=feedback.reply),
        HumanMessage(content=build_output_feedback(schema, feedback.diagnostics)),
    ]


def _start_structured_attempt(
    node: RoutineNode, runtime: Runtime | None, feedback: OutputFeedback | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = (
        feedback.record
        if feedback is not None
        else {"node": node.id, "kind": node.kind, "attempts": [], "outcome": "pending"}
    )
    attempt = {
        "attempt": _native_attempt(runtime, record),
        "kind": "feedback" if feedback is not None else "original",
    }
    record["attempts"].append(attempt)
    return record, attempt


def _record_transport_failure(exc: Exception, record: dict[str, Any]) -> None:
    record["outcome"] = "transport_failed"
    exc.node_output = record  # type: ignore[attr-defined]


def _tool_call_arguments(raw: Any) -> str:
    additional_kwargs = getattr(raw, "additional_kwargs", None)
    tool_calls = (
        additional_kwargs.get("tool_calls") if isinstance(additional_kwargs, dict) else None
    )
    arguments: list[str] = []
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            function = tool_call.get("function") if isinstance(tool_call, dict) else None
            value = function.get("arguments") if isinstance(function, dict) else None
            if isinstance(value, str):
                arguments.append(value)
    if arguments:
        return "\n".join(arguments)
    # Some adapters retain only LangChain's normalized call representations.
    for field in ("tool_calls", "invalid_tool_calls"):
        calls = getattr(raw, field, None)
        if not isinstance(calls, list):
            continue
        for call in calls:
            value = call.get("args") if isinstance(call, dict) else None
            if isinstance(value, str):
                arguments.append(value)
            elif isinstance(value, dict):
                arguments.append(json.dumps(value, allow_nan=False))
    return "\n".join(arguments)


def _validated_structured_output(
    *,
    node: RoutineNode,
    runtime: Runtime | None,
    schema: OutputSchema,
    outcome: dict[str, Any],
    record: dict[str, Any],
    attempt: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any]]:
    raw = outcome.get("raw")
    parsed = outcome.get("parsed")
    try:
        arguments = _tool_call_arguments(raw)
    except (TypeError, ValueError, RecursionError):
        arguments = ""
        attempt["reply_unavailable"] = True
    reply = arguments or normalize_message_content(getattr(raw, "content", ""))
    if not reply and parsed is not None:
        try:
            reply = json.dumps(parsed, ensure_ascii=False)
        except (TypeError, ValueError):
            attempt["reply_unavailable"] = True
    attempt["reply"] = reply
    check = schema.check_value(parsed) if parsed is not None else schema.check(reply)
    diagnostics = check.diagnostics
    if parsed is None and check.valid:
        diagnostics = [{"path": "$", "code": "provider_parse_failed"}]
    if parsed is not None and check.valid:
        record["outcome"] = "accepted"
        return parsed, raw, record

    attempt["diagnostics"] = diagnostics
    record["outcome"] = "validation_failed"
    raise NodeOutputValidationError(
        node.id,
        feedback=OutputFeedback(
            node_id=node.id,
            task_id=output_task_id(runtime),
            reply=reply,
            diagnostics=diagnostics,
            record=record,
        ),
    )


def _combined_system_prompt(*parts: str | None) -> str | None:
    prompt_parts = [part for part in parts if part]
    return "\n\n".join(prompt_parts) if prompt_parts else None


def _build_prompt_node(
    node: RoutineNode,
    *,
    model: BaseChatModel,
    base_system_prompt: str | None,
    clock_context: Callable[[], Awaitable[str]] | None = None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "prompt", config, "prompt_file")
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"prompt node {node.id}: config needs 'prompt_template'")
    node_system_prompt = config.get("system_prompt")
    declared_schema = config.get("response_schema")
    output_schema = OutputSchema(declared_schema, defs) if declared_schema is not None else None
    structured_model = (
        model.with_structured_output(
            {"title": "RoutineResponse", **output_schema.provider_schema()},
            method="json_schema",
            strict=True,
            include_raw=True,
        )
        if output_schema is not None
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
        system_prompt = _combined_system_prompt(
            base_system_prompt,
            node_system_prompt,
            await clock_context() if clock_context else None,
        )
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=rendered))
        _emit(runtime, "routine.node.started", {"node": node.id, "kind": node.kind})

        if structured_model is not None:
            assert output_schema is not None
            feedback, retry_messages = _retry_messages(node.id, runtime, output_schema)
            messages.extend(retry_messages)
            record, attempt = _start_structured_attempt(node, runtime, feedback)
            try:
                outcome = cast(dict[str, Any], await structured_model.ainvoke(messages))
            except Exception as exc:
                _record_transport_failure(exc, record)
                raise
            parsed, raw, record = _validated_structured_output(
                node=node,
                runtime=runtime,
                schema=output_schema,
                outcome=outcome,
                record=record,
                attempt=attempt,
            )
            _emit(runtime, "routine.node.completed", {"node": node.id, "kind": node.kind})
            return {
                "messages": [raw],
                "data": {output_key: parsed},
                "_node_output": record,
            }

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
    node: RoutineNode,
    *,
    model: BaseChatModel,
    clock_context: Callable[[], Awaitable[str]] | None = None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Ask the model to judge a rubric against a required structured schema."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    rubric_prompt = config["rubric_prompt"]
    inputs_config = config.get("inputs") or {}
    declared_schema = config.get("response_schema")
    if declared_schema is None:
        raise ValueError(f"classify node {node.id}: config needs 'response_schema'")
    output_schema = OutputSchema(declared_schema, defs)
    structured_model = model.with_structured_output(
        {"title": "RoutineResponse", **output_schema.provider_schema()},
        method="json_schema",
        strict=True,
        include_raw=True,
    )

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        data = state.get("data", {})
        if inputs_config:
            data = {**data, **_resolve_inputs(node.id, inputs_config, data)}
        try:
            rendered = rubric_prompt.format(**data)
        except KeyError as exc:
            raise KeyError(
                f"classify node {node.id}: rubric_prompt references missing state key: {exc}"
            ) from exc

        messages: list[BaseMessage] = []
        if clock_context:
            messages.append(SystemMessage(content=await clock_context()))
        messages.append(HumanMessage(content=rendered))
        feedback, retry_messages = _retry_messages(node.id, runtime, output_schema)
        messages.extend(retry_messages)
        record, attempt = _start_structured_attempt(node, runtime, feedback)
        try:
            outcome = cast(dict[str, Any], await structured_model.ainvoke(messages))
        except Exception as exc:
            _record_transport_failure(exc, record)
            raise
        parsed, raw, record = _validated_structured_output(
            node=node,
            runtime=runtime,
            schema=output_schema,
            outcome=outcome,
            record=record,
            attempt=attempt,
        )
        # `raw` carries usage_metadata -- threaded through `messages` like
        # every other model-calling node kind, purely so
        # _instrument_node_callable's uniform token-usage extraction (which
        # reads off `messages`, not any per-kind hook) sees it too.
        return {
            "messages": [raw],
            "data": {output_key: parsed},
            "_node_output": record,
        }

    return run
