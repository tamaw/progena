"""Compiles a validated :class:`RoutineGraph` into a runnable LangGraph ``StateGraph``.

Retry (per-node ``config.retry``, or a graph-wide default from ``settings.retry`` via
``StateGraph.set_node_defaults``), node caching, and a ``settings.recursion_limit``
backstop are all wired here; a router edge's own branches are how a graph author
expresses a *conditional* loop (see :func:`_build_router`) -- there is no separate
"loop" node kind or setting, since LangGraph's conditional-edge mechanism already
covers it and a parallel mechanism would just be a second way to say the same thing.
Nothing in the live progenitor runtime invokes a compiled routine yet -- ``compile_routine_graph``
produces a correct, independently testable runnable via ``.ainvoke(...)``, wiring it into
DeepAgent subagent delegation is a separate task.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.cache.memory import InMemoryCache
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, Command, RetryPolicy, Send, interrupt

from ..structured import validate_against_schema
from ..utils import utcnow_isoformat
from .expression import evaluate_expression
from .graph import (
    ROUTINE_NODE_RETRY_DEFAULTS,
    ROUTINE_NODE_TIMEOUT_SECONDS,
    ROUTINE_PROJECT_NODE_ID,
    ROUTINE_SEED_NODE_ID,
    RoutineContext,
    RoutineEdge,
    RoutineGraph,
    RoutineNode,
)

_SEED_NODE_ID = ROUTINE_SEED_NODE_ID

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _merge_data(existing: dict[str, Any] | None, update: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(existing) if existing else {}
    if update:
        merged.update(update)
    return merged


def _merge_run_meta(
    existing: dict[str, list[dict[str, Any]]] | None,
    update: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Per-node-id list of execution records, appended to (never replaced) --
    a looped node accumulates one entry per iteration, not just its latest."""
    merged = {node_id: list(entries) for node_id, entries in (existing or {}).items()}
    for node_id, entries in (update or {}).items():
        merged.setdefault(node_id, []).extend(entries)
    return merged


class RoutineState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    data: Annotated[dict[str, Any], _merge_data]
    # Wall-clock timing per node execution, recorded uniformly for every
    # node kind by _instrument_node_callable below -- independent of what
    # any individual node builder does or returns, so it can never be
    # skipped by omission the way a per-kind opt-in would be. Keyed by
    # node_id, one entry appended per execution (see _merge_run_meta).
    run_meta: Annotated[dict[str, list[dict[str, Any]]], _merge_run_meta]


def _node_config(node: RoutineNode) -> dict[str, Any]:
    """Present strict Pydantic config models to compiler helpers as plain data."""
    return node.config.model_dump(exclude_none=True)


def _emit(runtime: Runtime | None, event: str, payload: dict[str, Any]) -> None:
    if runtime is not None:
        runtime.stream_writer({"event": event, **payload})


def _context_data(runtime: Runtime | None) -> dict[str, Any]:
    context = getattr(runtime, "context", None) if runtime is not None else None
    return {"context": dict(context or {})}


def _retry_policy(config: dict[str, Any]) -> RetryPolicy | None:
    """Build a RetryPolicy from a ``retry`` sub-dict.

    ``config`` is either a node's own ``config`` or the graph's ``settings`` --
    both hold their retry tuning under the same ``retry`` key, so this one
    function builds both a per-node override and the graph-wide default passed
    to ``StateGraph.set_node_defaults``.
    """
    retry = config.get("retry")
    if not retry:
        return None
    defaults = ROUTINE_NODE_RETRY_DEFAULTS
    values: dict[str, Any] = {
        "initial_interval": float(retry.get("initial_interval", defaults["initial_interval"])),
        "backoff_factor": float(retry.get("backoff_factor", defaults["backoff_factor"])),
        "max_interval": float(retry.get("max_interval", defaults["max_interval"])),
        # The only place the two vocabularies meet: routines count retries so
        # that 0 plainly means "never retry", LangGraph counts total attempts.
        "max_attempts": int(retry.get("max_retries", defaults["max_retries"])) + 1,
        "jitter": bool(retry.get("jitter", defaults["jitter"])),
        # LangGraph's default declines to retry ValueError/TypeError/OSError and
        # friends as unfixable bugs. A routine node raising those is usually a
        # bad parse of a nondeterministic model response, which a retry does fix.
        "retry_on": Exception,
    }
    return RetryPolicy(**values)


def _cache_policy(config: dict[str, Any]) -> CachePolicy | None:
    cache = config.get("cache")
    if cache is None or cache is False:
        return None
    return CachePolicy(
        key_func=_cache_key,
        ttl=int(cache["ttl"]) if isinstance(cache, dict) and cache.get("ttl") is not None else None,
    )


def _node_timeout(config: dict[str, Any], settings: dict[str, Any]) -> float | None:
    """Seconds a node may run for, resolved node-first then graph-wide.

    Mirrors how `retry` resolves: the node's own value wins if it declares one,
    otherwise the graph's. Every node carries a limit by default, because the
    common failure of an agent step is not raising but hanging. An explicit 0
    is the way to say a step is allowed to take as long as it takes.
    """
    for source in (config, settings):
        if "timeout" in source:
            timeout = float(source["timeout"])
            return timeout or None
    return ROUTINE_NODE_TIMEOUT_SECONDS


_RESERVED_STATE_CHANNELS = frozenset({"messages", "data"})


def _extra_state_fields(*schemas: dict[str, str] | list[str] | None) -> list[str]:
    """Union of declared input/output field names (order-stable, de-duplicated,
    reserved base channel names dropped -- a graph author declaring "data" or
    "messages" in input_schema/output_schema just means that base channel,
    not a second one). Accepts either a flat name->type map (output_schema's
    shape) or an already-extracted name list (input_schema's real-JSON-Schema
    shape -- see _input_schema_field_names): iterating either yields names."""
    seen = set(_RESERVED_STATE_CHANNELS)
    names: list[str] = []
    for schema in schemas:
        for name in schema or {}:
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def _input_schema_field_names(input_schema: dict[str, Any] | None) -> list[str]:
    """Declared parameter names from a real-JSON-Schema-shaped input_schema
    (``{"type": "object", "properties": {...}, "required": [...]}``, the same
    convention node-level ``response_schema`` uses) -- everything downstream
    (state channels, the compiled input TypedDict, the seed node) only ever
    needs the names, not the property definitions themselves."""
    return list((input_schema or {}).get("properties") or {})


def _extended_state_type(extra_fields: list[str]) -> Any:
    """RoutineState plus one extra Any-typed top-level channel per declared
    input/output field, so the seed unpacker / output projector (and any
    node) can read or write them like any other state key. Only built when a
    graph actually declares extra fields -- the common (no schema) case keeps
    using the static RoutineState untouched."""
    if not extra_fields:
        return RoutineState
    typed_dict_factory: Any = TypedDict
    annotations: dict[str, Any] = {
        "messages": Annotated[list[BaseMessage], add_messages],
        "data": Annotated[dict[str, Any], _merge_data],
        **dict.fromkeys(extra_fields, Any),
    }
    return typed_dict_factory("RoutineOverallState", annotations, total=False)


def _input_schema_type(input_fields: list[str]) -> Any:
    """``messages`` is always accepted alongside whatever input_schema
    declares -- every routine invocation is driven by a conversational turn,
    input_schema only adds structured fields on top of it."""
    if not input_fields:
        return None
    typed_dict_factory: Any = TypedDict
    annotations: dict[str, Any] = {
        "messages": Annotated[list[BaseMessage], add_messages],
        **dict.fromkeys(input_fields, Any),
    }
    return typed_dict_factory("RoutineInputState", annotations, total=False)


def _output_schema_type(output_fields: list[str]) -> Any:
    """``messages`` is always returned alongside whatever output_schema
    declares. Nested routine-as-subagent delegation (_build_subagent_node /
    _build_agent_node below) and the top-level CompiledSubAgent proxy
    (subagent.py's ``_run``) both unconditionally read ``result["messages"]``
    -- an output_schema that dropped it would silently break delegation for
    any routine invoked as a subagent."""
    if not output_fields:
        return None
    typed_dict_factory: Any = TypedDict
    annotations: dict[str, Any] = {
        "messages": Annotated[list[BaseMessage], add_messages],
        **dict.fromkeys(output_fields, Any),
    }
    return typed_dict_factory("RoutineOutputState", annotations, total=False)


def _cache_key(value: Any) -> str:
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key == "messages" and isinstance(item, list):
                normalized[key] = [
                    {
                        "type": getattr(message, "type", ""),
                        "content": normalize_message_content(getattr(message, "content", "")),
                    }
                    for message in item
                ]
            else:
                normalized[key] = item
        return json.dumps(normalized, sort_keys=True, default=str)
    return repr(value)


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


def _message_with_usage(text: str, source: BaseMessage | None) -> AIMessage:
    """A fresh AIMessage carrying `text` (already normalized off `source`,
    which may be a Responses-API block list `source.content` isn't) --
    preserves `source`'s usage_metadata rather than dropping it, so
    _instrument_node_callable's uniform token-usage extraction (reads
    `messages`, see factory.py) still sees a real LLM call's usage even
    though this node reconstructs the message instead of returning `source`
    verbatim."""
    return AIMessage(content=text, usage_metadata=getattr(source, "usage_metadata", None))


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


def _resolve_inputs(
    node_id: str,
    inputs_config: dict[str, str],
    data: dict[str, Any],
) -> dict[str, Any]:
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


def _combined_system_prompt(
    base_system_prompt: str | None, node_system_prompt: str | None
) -> str | None:
    parts = [p for p in (base_system_prompt, node_system_prompt) if p]
    return "\n\n".join(parts) if parts else None


def _seed_node(input_fields: list[str] | None = None) -> Any:
    fields = input_fields or []

    async def seed(state: dict[str, Any], runtime: Runtime | None = None) -> dict[str, Any]:
        messages = state.get("messages") or []
        request = normalize_message_content(messages[-1].content) if messages else ""
        seeded: dict[str, Any] = {"request": request, **_context_data(runtime)}
        for field in fields:
            value = state.get(field)
            if value is not None:
                seeded[field] = value
        _emit(runtime, "routine.seed", {"request": request})
        return {"data": seeded}

    return seed


_DEFS_REF_PREFIX = "#/$defs/"


def _resolve_response_schema(
    node_id: str, kind: str, config: dict[str, Any], defs: dict[str, dict[str, Any]] | None
) -> dict[str, Any] | None:
    """Resolves config.response_schema, following a ``{"$ref": "#/$defs/<name>"}``
    indirection into the graph document's own top-level ``$defs`` -- the same reuse
    mechanism JSON Schema itself uses, scoped to this routine graph rather than the
    graph.routine.schema.v1.json meta-schema (which has its own, unrelated $defs)."""
    schema: dict[str, Any] | None = config.get("response_schema")
    if schema is None:
        return None
    ref = schema.get("$ref") if isinstance(schema, dict) else None
    if ref is None:
        return schema
    if not ref.startswith(_DEFS_REF_PREFIX):
        raise ValueError(
            f"{kind} node {node_id}: response_schema.$ref must be of the form "
            f"'#/$defs/<name>', got {ref!r}"
        )
    name = ref[len(_DEFS_REF_PREFIX) :]
    resolved = (defs or {}).get(name)
    if resolved is None:
        raise ValueError(
            f"{kind} node {node_id}: response_schema.$ref points at undefined '$defs.{name}'"
        )
    return resolved


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
            response_schema, method="json_schema", strict=True, include_raw=True
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
        response_schema, method="json_schema", strict=True, include_raw=True
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


def _build_agent_node(
    node: RoutineNode,
    *,
    agent_registry: dict[str, Any],
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
        if agent_name not in agent_registry:
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

        target = agent_registry[agent_name]
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


def _build_foreach_node(node: RoutineNode, *, agent_registry: dict[str, Any]) -> Any:
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
        target = agent_registry.get(agent_name)
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


def _build_routine_node(node: RoutineNode, *, routine_registry: dict[str, Any]) -> Any:
    config = _node_config(node)
    output_key = node.output_key or node.id
    name = config["routine_name"]
    input_map = config.get("input_map") or {}
    output_map = config.get("output_map") or {}

    async def run(state: RoutineState, runtime: Runtime | None = None) -> dict[str, Any]:
        target = routine_registry.get(name)
        if target is None:
            raise KeyError(f"node {node.id}: unknown routine '{name}'")
        parent_data = state.get("data", {})
        child_data = (
            _resolve_inputs(node.id, input_map, parent_data) if input_map else dict(parent_data)
        )
        child_state = {"messages": state.get("messages") or [], "data": child_data}
        if runtime is None:
            result = await target.ainvoke(child_state)
        else:
            result = await target.ainvoke(child_state, context=runtime.context)
        messages = result.get("messages") or []
        text = normalize_message_content(messages[-1].content) if messages else ""
        result_data = result.get("data") or {}
        mapped_output = _resolve_inputs(node.id, output_map, result_data) if output_map else {}
        return {"messages": [AIMessage(content=text)], "data": {**mapped_output, output_key: text}}

    return run


def _build_send_router(node: RoutineNode, target: str) -> Callable[[RoutineState], list[Send]]:
    config = _node_config(node)
    item_key = config["item_key"]

    def route(state: RoutineState) -> list[Send]:
        items = _foreach_items(node, state)
        data = state.get("data", {})
        return [
            Send(
                target, {"messages": state.get("messages") or [], "data": {**data, item_key: item}}
            )
            for item in items
        ]

    return route


def _build_node_callable(
    node: RoutineNode,
    *,
    model: BaseChatModel,
    base_system_prompt: str | None,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    tool_registry: Any = None,
    defs: dict[str, dict[str, Any]] | None = None,
) -> Any:
    if node.kind == "prompt":
        return _build_prompt_node(
            node, model=model, base_system_prompt=base_system_prompt, defs=defs
        )
    if node.kind == "eval":
        return _build_eval_node(node)
    if node.kind == "condition":
        return _build_condition_node(node)
    if node.kind == "switch":
        return _build_switch_node(node)
    if node.kind == "split":
        return _build_split_node(node)
    if node.kind == "classify":
        return _build_classify_node(node, model=model, defs=defs)
    if node.kind == "agent":
        return _build_agent_node(node, agent_registry=agent_registry, defs=defs)
    if node.kind == "a2a":
        return _build_a2a_node(node, tool_registry=tool_registry)
    if node.kind == "foreach":
        return _build_foreach_node(node, agent_registry=agent_registry)
    if node.kind == "human":
        return _build_human_node(node)
    if node.kind == "routine":
        return _build_routine_node(node, routine_registry=routine_registry)
    if node.kind == "noop":
        return _build_noop_node(node)
    raise ValueError(f"unknown node kind: {node.kind}")


def _extract_token_usage(result: dict[str, Any] | None) -> dict[str, int] | None:
    """Sums ``usage_metadata`` off every ``AIMessage`` a node returned in
    ``messages`` -- the same uniform choke point as duration, so no node
    kind has to opt in individually. Every model-calling node builder
    already threads its raw response through ``messages`` (prompt/classify/
    agent/a2a/routine); kinds with no LLM call (eval/condition/switch/split/
    noop/human/foreach-send) correctly report no tokens by having no
    messages at all. Returns ``None`` -- not a zeroed dict -- when nothing
    carried usage data, so a display layer can tell "no LLM call" apart
    from "LLM call, provider didn't report usage".
    """
    if not isinstance(result, dict):
        return None
    messages = result.get("messages")
    if not messages:
        return None
    input_tokens = output_tokens = total_tokens = 0
    found = False
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            continue
        found = True
        input_tokens += usage.get("input_tokens") or 0
        output_tokens += usage.get("output_tokens") or 0
        total_tokens += usage.get("total_tokens") or 0
    if not found:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _instrument_node_callable(node_id: str, fn: Any) -> Any:
    """Wrap a node's runnable to record wall-clock duration (and, when the
    node made a real LLM call, token usage) into run_meta, uniformly for
    every node kind -- independent of what the node itself returns, so it
    can never be skipped by omission the way a per-kind opt-in would be.
    Preserves the inner callable's calling convention: some node builders
    accept an optional ``runtime`` second parameter and LangGraph decides
    whether to pass it based on the callable's own signature, so this
    wrapper inspects that signature once at wrap time and forwards the
    same way.

    A node that raises never gets to *return* its run_meta entry -- LangGraph
    only applies (and checkpoints) a node's state update when it returns
    normally, so a raised exception would otherwise leave this node with no
    run_meta entry at all despite having run for a real, measurable
    duration. Stash ``(node_id, entry)`` on the exception itself instead
    (`routine_run_meta`) so the caller that already catches routine-run
    failures -- ``subagent.py``'s ``_run``, which alone has the compiled
    graph and this thread's ``run_config`` in scope -- can persist it via
    ``compiled.aupdate_state`` before converting the failure into a chat
    message. The exception's own type/message are left untouched so that
    existing catch clause keeps matching exactly as before.
    """
    takes_runtime = len(inspect.signature(fn).parameters) >= 2

    async def wrapped(state: RoutineState, runtime: Any = None) -> dict[str, Any]:
        started = time.monotonic()
        started_at = utcnow_isoformat()
        try:
            result = await (fn(state, runtime) if takes_runtime else fn(state))
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            entry = {
                "started_at": started_at,
                "duration_ms": round(duration_ms, 2),
                "error": str(exc) or type(exc).__name__,
            }
            exc.routine_run_meta = (node_id, entry)  # type: ignore[attr-defined]
            raise
        duration_ms = (time.monotonic() - started) * 1000
        update = dict(result) if isinstance(result, dict) else {}
        entry: dict[str, Any] = {"started_at": started_at, "duration_ms": round(duration_ms, 2)}
        tokens = _extract_token_usage(result)
        if tokens is not None:
            entry["tokens"] = tokens
        update["run_meta"] = {node_id: [entry]}
        return update

    return wrapped


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


def compile_routine_graph(
    graph: RoutineGraph,
    *,
    model: BaseChatModel,
    agent_registry: dict[str, Any] | None = None,
    routine_registry: dict[str, Any] | None = None,
    tool_registry: Any = None,
    base_system_prompt: str | None = None,
    checkpointer: Any | None = None,
    cache: Any | None = None,
):
    """Compile a routine with LangGraph persistence, retries, context and fan-out.

    The graph document controls optional node retries/caches and recursion limits;
    callers provide the checkpointer and invocation context when resumability or
    human approval is required.
    """
    agent_registry = agent_registry or {}
    routine_registry = routine_registry or {}

    node_ids = {node.id for node in graph.nodes}
    if graph.entry not in node_ids:
        raise ValueError(f"entry references unknown node: {graph.entry}")

    input_fields = _input_schema_field_names(graph.input_schema)
    output_fields = list(graph.output_schema or {})

    builder = StateGraph(
        _extended_state_type(_extra_state_fields(input_fields, graph.output_schema)),
        context_schema=RoutineContext,
        input_schema=_input_schema_type(input_fields),
        output_schema=_output_schema_type(output_fields),
    )
    settings = graph.settings.model_dump(exclude_none=True)
    # A fan_in join's declared sources are almost always mutually-exclusive
    # router branches -- at most one of them runs per invocation. Deferring
    # the join node makes it fire once every source that *did* run this
    # invocation has finished. Without it a join whose branches are unequal
    # lengths fires once per arriving branch instead of once in total.
    fan_in_targets = {edge.target_ids[0] for edge in graph.edges if edge.level == "fan_in"}
    # Where each node's failure goes, declared as an edge rather than buried in
    # the node's retry block, so failure paths are visible to anything that
    # reads the topology.
    error_routes = {
        edge.source_ids[0]: edge.target_ids[0] for edge in graph.edges if edge.level == "error"
    }
    builder.add_node(_SEED_NODE_ID, _seed_node(input_fields))
    builder.add_edge(START, _SEED_NODE_ID)
    builder.add_edge(_SEED_NODE_ID, graph.entry)

    for node in graph.nodes:
        config = _node_config(node)
        retry_config = config if "retry" in config else settings
        recovery_target = error_routes.get(node.id)
        node_options: dict[str, Any] = {
            "retry_policy": _retry_policy(retry_config),
            "cache_policy": _cache_policy(config),
            "defer": node.id in fan_in_targets,
            "timeout": _node_timeout(config, settings),
        }
        if recovery_target is not None:
            node_options["error_handler"] = _build_retry_exhausted_handler(node.id, recovery_target)
            node_options["destinations"] = (recovery_target,)
        builder.add_node(
            node.id,
            _instrument_node_callable(
                node.id,
                _build_node_callable(
                    node,
                    model=model,
                    base_system_prompt=base_system_prompt,
                    agent_registry=agent_registry,
                    routine_registry=routine_registry,
                    tool_registry=tool_registry,
                    defs=graph.defs,
                ),
            ),
            **node_options,
        )

    # Every path that would otherwise go straight to END routes through the
    # projector instead when output_schema is declared, so its declared
    # fields become real top-level channels before the graph actually ends
    # -- see _build_output_projector.
    terminal_target = END
    if output_fields:
        builder.add_node(ROUTINE_PROJECT_NODE_ID, _build_output_projector(output_fields))
        builder.add_edge(ROUTINE_PROJECT_NODE_ID, END)
        terminal_target = ROUTINE_PROJECT_NODE_ID

    # Error edges are wired as the node's error_handler above, not here: they
    # are not successors, so they neither occupy a node's outgoing slot nor
    # stop an otherwise-terminal node from reaching the end of the graph.
    flow_edges = [edge for edge in graph.edges if edge.is_flow]
    outgoing: dict[str, RoutineEdge] = {}
    for edge in flow_edges:
        for source in edge.source_ids:
            if source in outgoing:
                raise ValueError(f"node has more than one outgoing edge declaration: {source}")
            outgoing[source] = edge

    foreach_send_ids = {
        node.id
        for node in graph.nodes
        if node.kind == "foreach" and _node_config(node)["mode"] == "send"
    }
    split_ids = {node.id for node in graph.nodes if node.kind == "split"}
    for node in graph.nodes:
        if node.id not in outgoing:
            builder.add_edge(node.id, terminal_target)

    for edge in flow_edges:
        if edge.level == "route":
            targets = list((edge.branches or {}).values())
            if edge.default is not None:
                targets.append(edge.default)
            source = edge.source_ids[0]
            builder.add_conditional_edges(
                source,
                _build_split_router(edge) if source in split_ids else _build_router(edge),
                path_map={target: target for target in targets},
            )
            continue

        if edge.level == "fan_in":
            # Per-source edges, not a single list-form add_edge: the target
            # is deferred (see fan_in_targets above), so it fires once
            # whichever sources actually ran this invocation are done --
            # list-form add_edge instead creates a strict "wait for every
            # listed source" barrier that a mutually-exclusive router branch
            # can never satisfy.
            target = edge.target_ids[0]
            for source in edge.source_ids:
                builder.add_edge(source, target)
            continue

        source = edge.source_ids[0]
        targets = edge.target_ids
        if source in foreach_send_ids:
            if edge.level != "normal" or len(targets) != 1 or edge.loop_guard is not None:
                raise ValueError(
                    f"foreach Send node {source} requires one unguarded normal outgoing edge"
                )
            builder.add_conditional_edges(
                source,
                _build_send_router(
                    next(node for node in graph.nodes if node.id == source), targets[0]
                ),
                path_map={targets[0]: targets[0]},
            )
            continue

        if edge.loop_guard is not None:
            false_target = edge.loop_guard.on_false or terminal_target
            builder.add_conditional_edges(
                source,
                _build_loop_guard(edge, terminal_target),
                path_map={target: target for target in [*targets, false_target]},
            )
            continue

        for target in targets:
            builder.add_edge(source, target)

    if settings.get("cache") and cache is None:
        cache = InMemoryCache()
    compiled = builder.compile(checkpointer=checkpointer, cache=cache)
    limit = settings.get("recursion_limit")
    if limit is not None:
        compiled = compiled.with_config({"recursion_limit": int(limit)})
    return compiled
