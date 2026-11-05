"""Object-agent wait/background, A2A peer, and nested-routine delegation builders."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from src.progena.llm.structured import validate_against_schema

from ..instructions.templates import (
    instruction_relative_path,
    parse_instruction_template,
    render_instruction_template,
)
from ..threads import routine_agent_thread_id
from .approvals import approval_decision_id
from .control_nodes import _foreach_items
from .graph import RoutineNode
from .messages import _message_with_usage, extract_json_object, normalize_message_content
from .node_output import (
    NodeOutputRetryError,
    NodeOutputSchemaError,
    NodeOutputValidationError,
    OutputFeedback,
    OutputSchema,
    build_output_feedback,
    get_output_feedback,
    output_task_id,
)
from .state import (
    RoutineState,
    _emit,
    _node_config,
    _require_no_file_source,
    _resolve_inputs,
    _resolve_response_schema,
)

_routine_ancestry_var: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "_routine_ancestry", default=()
)


def _a2a_peer_fingerprint(agent_config: dict[str, Any]) -> str:
    """Return a hashed continuity identity without persisting peer configuration."""
    peer = {
        "transport": str(agent_config.get("transport") or "").strip().lower(),
        "url": str(agent_config.get("url") or "").strip(),
    }
    serialized = json.dumps(peer, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _build_object_agent_node(
    node: RoutineNode,
    *,
    object_agent_registry: dict[str, Any],
    defs: dict[str, dict[str, Any]] | None = None,
    instruction_loader: Callable[[str], str] | None = None,
    background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> Any:
    """Conversational delegation to a local object agent, keyed to stay on the
    same checkpointer thread across repeat invocations of this node within one
    routine run (``continue_thread``, default on) rather than starting a fresh
    conversation each time."""
    config = _node_config(node)
    output_key = node.output_key or node.id
    agent_name = config["agent_name"]
    template = config.get("prompt_template")
    instruction = config.get("instruction")
    inputs_config = config.get("inputs") or {}
    if not template and not instruction and not inputs_config:
        raise ValueError(
            f"agent node {node.id}: config needs 'prompt_template', 'instruction', or 'inputs'"
        )
    if instruction and instruction_loader is None:
        raise ValueError(f"agent node {node.id}: instruction '{instruction}' cannot be loaded")
    response_schema = _resolve_response_schema(node.id, "agent", config, defs)
    continue_thread = config.get("continue_thread", True)

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        if agent_name not in object_agent_registry:
            raise KeyError(f"node {node.id}: unknown agent '{agent_name}'")
        data = state.get("data", {})
        if inputs_config:
            namespace = _resolve_inputs(node.id, inputs_config, data)
            rendered = json.dumps(namespace)
        elif instruction:
            assert instruction_loader is not None  # guaranteed by the guard above
            path = instruction_relative_path(instruction)
            try:
                source = instruction_loader(path)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                raise ValueError(
                    f"agent node {node.id}: instruction '{instruction}' not found"
                ) from exc
            if source is None:
                raise ValueError(f"agent node {node.id}: instruction '{instruction}' not found")
            rendered = render_instruction_template(parse_instruction_template(source), data)
        else:
            assert template is not None  # guaranteed by the guard above
            try:
                rendered = template.format(**data)
            except KeyError as exc:
                raise KeyError(
                    f"agent node {node.id}: template references missing state key: {exc}"
                ) from exc

        if config.get("execution", "wait") == "background":
            if background_runner is None:
                raise ValueError(f"agent node {node.id}: background execution is unavailable")
            context = getattr(runtime, "context", None) if runtime is not None else None
            reference = await background_runner(
                agent_name,
                rendered,
                parent_thread_id=(context or {}).get("request_id"),
                node_id=node.id,
                response_schema=response_schema,
                model_profile=config.get("model_profile"),
            )
            return {"data": {output_key: reference}}

        target = object_agent_registry[agent_name]
        invoke_kwargs: dict[str, Any] = {}
        if continue_thread:
            context = getattr(runtime, "context", None) if runtime is not None else None
            correlation = (context or {}).get("request_id") or "routine"
            invoke_kwargs["config"] = {
                "configurable": {"thread_id": routine_agent_thread_id(correlation, node.id)}
            }
        if config.get("model_profile"):
            invoke_kwargs.setdefault("config", {}).setdefault("configurable", {})[
                "model_profile"
            ] = config["model_profile"]
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


def _build_a2a_node(
    node: RoutineNode,
    *,
    tool_registry: Any,
    timeout: float | None = None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build one native A2A dispatch per node invocation.

    Schema failures become ordinary LangGraph model-response retries. The
    retry callback scopes feedback to this exact runtime task, so a retry
    continues the peer's A2A context instead of replaying the original action.
    """
    from src.progena.a2a.client import A2AClient
    from src.progena.a2a.payloads import (
        classify_a2a_reply,
        extract_message_text,
        summarize_task_payload,
    )

    config = _node_config(node)
    output_key = node.output_key or node.id
    _require_no_file_source(node.id, "a2a", config, "prompt_file")
    agent_name = config["agent_name"]
    template = config.get("prompt_template")
    if not template:
        raise ValueError(f"a2a node {node.id}: config needs 'prompt_template'")
    schema: OutputSchema | None = None
    declared_schema = config.get("response_schema")
    if declared_schema is not None:
        try:
            schema = OutputSchema(declared_schema, defs)
        except NodeOutputSchemaError as exc:
            raise NodeOutputSchemaError(f"a2a node {node.id}: {exc}") from exc

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

    def _native_attempt(runtime: Runtime | None, record: dict[str, Any]) -> int:
        execution = getattr(runtime, "execution_info", None) if runtime is not None else None
        attempt = getattr(execution, "node_attempt", None)
        return attempt if isinstance(attempt, int) else len(record["attempts"]) + 1

    def _record_error(error: Exception, record: dict[str, Any]) -> Exception:
        error.node_output = record  # type: ignore[attr-defined]
        return error

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

        peer_fingerprint = _a2a_peer_fingerprint(cfg)

        context = getattr(runtime, "context", None) if runtime is not None else None
        metadata = {
            "caller": "routine",
            "routing_mode": "routine_node",
            "thread_id": (context or {}).get("request_id"),
        }
        effective_timeout = (
            timeout if timeout is not None and timeout > 0 else _A2A_NO_LIMIT_TIMEOUT
        )

        # Contract-free A2A nodes keep their established lenient behavior.
        if schema is None:
            response = await A2AClient().send_message(
                cfg,
                rendered,
                blocking=True,
                timeout=effective_timeout,
                context=metadata,
            )
            if not response.get("success"):
                raise ValueError(f"a2a node {node.id}: {response.get('error') or 'unknown error'}")
            text = _extract_reply_text(response.get("result"))
            output: dict[str, Any] = {output_key: text}
            parsed = extract_json_object(text)
            if parsed is not None:
                output[f"{output_key}_json"] = parsed
            return {"messages": [AIMessage(content=text)], "data": output}

        feedback = get_output_feedback(node.id, runtime)
        record = (
            feedback.record
            if feedback is not None
            else {
                "node": node.id,
                "kind": "a2a",
                "peer": agent_name,
                "peer_fingerprint": peer_fingerprint,
                "attempts": [],
                "outcome": "pending",
            }
        )
        attempt: dict[str, Any] = {
            "attempt": _native_attempt(runtime, record),
            "kind": "feedback" if feedback is not None else "original",
        }
        record["attempts"].append(attempt)

        if feedback is not None and record.get("peer_fingerprint") != peer_fingerprint:
            attempt["peer_changed"] = True
            record["outcome"] = "feedback_peer_changed"
            raise NodeOutputRetryError(
                f"a2a node {node.id}: peer changed before output feedback",
                code="feedback_peer_changed",
                record=record,
            )

        try:
            if feedback is None:
                response = await A2AClient().send_message(
                    cfg,
                    rendered,
                    blocking=True,
                    timeout=effective_timeout,
                    context=metadata,
                )
            else:
                if feedback.context_id is None:
                    record["outcome"] = "no_protocol_context"
                    raise NodeOutputRetryError(
                        f"a2a node {node.id}: completed peer reply had no A2A context",
                        code="no_protocol_context",
                        record=record,
                    )
                response = await A2AClient().send_message(
                    cfg,
                    build_output_feedback(schema, feedback.diagnostics),
                    blocking=True,
                    timeout=effective_timeout,
                    context=metadata,
                    task_id=None,
                    context_id=feedback.context_id,
                )
        except asyncio.CancelledError:
            raise
        except NodeOutputRetryError:
            raise
        except Exception as exc:
            attempt["send_failed"] = True
            record["outcome"] = "feedback_send_failed" if feedback is not None else "send_failed"
            if feedback is not None:
                raise NodeOutputRetryError(
                    f"a2a node {node.id}: peer did not accept output feedback",
                    code="feedback_send_failed",
                    record=record,
                ) from exc
            raise _record_error(
                ValueError(f"a2a node {node.id}: peer request failed"), record
            ) from exc

        if not response.get("success"):
            attempt["send_failed"] = True
            record["outcome"] = "feedback_send_failed" if feedback is not None else "send_failed"
            if feedback is not None:
                raise NodeOutputRetryError(
                    f"a2a node {node.id}: peer did not accept output feedback",
                    code="feedback_send_failed",
                    record=record,
                )
            raise _record_error(ValueError(f"a2a node {node.id}: peer request failed"), record)

        reply = classify_a2a_reply(response.get("result"))
        attempt.update(
            reply_kind=reply.kind,
            task_state=reply.state,
            task_id=reply.task_id,
            context_id=reply.context_id,
            reply_message_id=reply.message_id,
            reply=reply.text,
        )
        if not reply.completed:
            record["outcome"] = "not_completed"
            if feedback is not None:
                raise NodeOutputRetryError(
                    f"a2a node {node.id}: output feedback did not complete",
                    code="feedback_not_completed",
                    record=record,
                )
            raise _record_error(
                ValueError(f"a2a node {node.id}: peer reply not completed (state '{reply.state}')"),
                record,
            )
        if feedback is not None and reply.context_id != feedback.context_id:
            record["outcome"] = "context_mismatch"
            raise NodeOutputRetryError(
                f"a2a node {node.id}: output feedback left its A2A context",
                code="context_mismatch",
                record=record,
            )

        check = schema.check(reply.text)
        attempt["valid"] = check.valid
        if check.valid:
            assert check.value is not None
            record["outcome"] = "accepted"
            return {
                "messages": [AIMessage(content=reply.text)],
                "data": {output_key: reply.text, f"{output_key}_json": check.value},
                "_node_output": record,
            }

        attempt["diagnostics"] = check.diagnostics
        record["outcome"] = "validation_failed"
        if reply.context_id is None:
            raise NodeOutputRetryError(
                f"a2a node {node.id}: completed peer reply had no A2A context",
                code="no_protocol_context",
                record=record,
            )
        raise NodeOutputValidationError(
            node.id,
            feedback=OutputFeedback(
                node_id=node.id,
                task_id=output_task_id(runtime),
                reply=reply.text,
                diagnostics=check.diagnostics,
                record=record,
                context_id=feedback.context_id if feedback is not None else reply.context_id,
            ),
        )

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


_ROUTINE_MAX_NESTING_DEPTH = 10
_A2A_NO_LIMIT_TIMEOUT = 3600.0  # 1 hour — large finite stand-in for "unlimited"


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
        ancestry = _routine_ancestry_var.get()
        if name in ancestry:
            cycle = " -> ".join(ancestry + (name,))
            raise ValueError(f"node {node.id}: nested routine cycle detected: {cycle}")
        if len(ancestry) >= _ROUTINE_MAX_NESTING_DEPTH:
            raise ValueError(
                f"node {node.id}: nested routine depth exceeds limit "
                f"({_ROUTINE_MAX_NESTING_DEPTH}): {' -> '.join(ancestry)} -> {name}"
            )
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
        pending = (context or {}).get("_routine_pending_children", {}).get(node.id)
        child_name = name

        async def invoke() -> dict[str, Any]:
            token = _routine_ancestry_var.set(ancestry + (name,))
            try:
                if routine_runner is not None:
                    return await routine_runner(child_name, child_state, runtime)
                target = routine_registry.get(child_name)
                if target is None:
                    raise KeyError(f"node {node.id}: unknown routine '{name}'")
                if runtime is None:
                    return await target.ainvoke(child_state)
                return await target.ainvoke(child_state, context=runtime.context)
            finally:
                _routine_ancestry_var.reset(token)

        result = None if pending else await invoke()
        while pending or (
            result is not None and (result.get("_routine_paused") or result.get("__interrupt__"))
        ):
            if pending:
                # LangGraph replays ALL prior interrupt answers in this node.
                # Each answer carries its original child approval so an old
                # approve cannot accidentally answer a later approval.
                decision = interrupt(pending)
                approval = decision["approval"]
                invocation = approval["child_invocation"]
                child_name = invocation["agent_id"]
                child_state.update(
                    _interactive_session_id=invocation["session_id"],
                    routine_revisions={invocation["agent_id"]: invocation["revision_id"]},
                    _routine_resume={
                        "thread_id": invocation["thread_id"],
                        "interrupt_id": approval["child_interrupt_id"],
                        "answer": decision["_routine_nested_answer"],
                        "approval": approval["child_approval"],
                    },
                )
                result = await invoke()
                pending = None
                if not (result.get("_routine_paused") or result.get("__interrupt__")):
                    break
            assert result is not None
            invocation = result.get("_routine_invocation")
            interrupts = result.get("__interrupt__") or []
            if not invocation or not interrupts:
                raise ValueError(f"node {node.id}: paused child requires a resumable approval")
            child_interrupt = interrupts[0]
            payload = child_interrupt.value
            pending = {
                "node": node.id,
                "message": payload.get("message", "Child routine is awaiting approval"),
                "allowed_responses": payload.get("allowed_responses"),
                "child_invocation": invocation,
                "child_interrupt_id": approval_decision_id(child_interrupt),
                "child_approval": payload,
            }
        assert result is not None
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
