"""Compiles a validated :class:`RoutineGraph` into a runnable LangGraph ``StateGraph``.

Retry (per-node ``config.retry`` or execution-policy defaults), node caching, and a graph-step
backstop are all wired here; a router edge's own branches are how a graph author
expresses a *conditional* loop (see :func:`_build_router`) -- there is no separate
"loop" node kind or setting, since LangGraph's conditional-edge mechanism already
covers it and a parallel mechanism would just be a second way to say the same thing.
The live runtime resolves routine documents from Postgres and uses an ephemeral
runner to compile and invoke them. ``compile_routine_graph`` remains the
independently testable compiler, while conversational DeepAgent subagent
discovery is wired separately by the agent factory.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.cache.memory import InMemoryCache
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.types import CachePolicy, RetryPolicy, TimeoutPolicy

from src.progena.settings.policies import CACHE_NODE_KINDS, PolicyValues

from ..utils import utcnow_isoformat

# Re-export the existing helper imports for compiler callers.
from .control_nodes import (
    _build_condition_node,
    _build_eval_node,
    _build_human_node,
    _build_loop_guard,
    _build_noop_node,
    _build_output_projector,
    _build_reduce_node,
    _build_retry_exhausted_handler,
    _build_review_provenance_node,
    _build_router,
    _build_send_router,
    _build_split_node,
    _build_split_router,
    _build_switch_node,
    _seed_node,
)
from .delegation_nodes import (
    _build_a2a_node,
    _build_foreach_node,
    _build_object_agent_node,
    _build_routine_node,
)
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
from .messages import (
    extract_json_object as extract_json_object,
)
from .messages import (
    normalize_message_content as normalize_message_content,
)
from .model_nodes import _build_classify_node, _build_prompt_node
from .provenance import ReviewEvidenceSource
from .state import (
    RoutineState as RoutineState,
)
from .state import (
    _merge_data,
    _node_config,
)
from .state import (
    resolve_data_path as resolve_data_path,
)

_SEED_NODE_ID = ROUTINE_SEED_NODE_ID


def _retry_model_response(exc: Exception) -> bool:
    predicate = RetryPolicy().retry_on
    return isinstance(exc, ValueError) or bool(callable(predicate) and predicate(exc))


def _retry_policy(config: dict[str, Any]) -> RetryPolicy | None:
    """Build a RetryPolicy from a ``retry`` sub-dict.

    The compiler merges execution-policy defaults with explicit node values
    before building the native LangGraph retry policy.
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
        "retry_on": (
            Exception
            if retry.get("retry_on", "transient") == "all"
            else _retry_model_response
            if retry.get("retry_on") == "model_response"
            else RetryPolicy().retry_on
        ),
    }
    return RetryPolicy(**values)


def _cache_policy(config: dict[str, Any], namespace: str = "") -> CachePolicy | None:
    cache = config.get("cache")
    if cache is None or cache is False:
        return None
    return CachePolicy(
        key_func=lambda value: namespace + _cache_key(value),
        ttl=int(cache["ttl"]) if isinstance(cache, dict) and cache.get("ttl") is not None else None,
    )


def _node_timeout(config: dict[str, Any], policy_defaults: dict[str, Any]) -> float | None:
    """Seconds a node may run for, resolved node-first then graph-wide.

    Mirrors how `retry` resolves: the node's own value wins if it declares one,
    otherwise the graph's. Every node carries a limit by default, because the
    common failure of an agent step is not raising but hanging. An explicit 0
    is the way to say a step is allowed to take as long as it takes.
    """
    for source in (config, policy_defaults):
        if "timeout" in source:
            timeout = float(source["timeout"])
            return timeout or None
    return ROUTINE_NODE_TIMEOUT_SECONDS


_RESERVED_STATE_CHANNELS = frozenset({"messages", "data"})


def _extra_state_fields(*name_lists: list[str] | None) -> list[str]:
    """Union of declared input/output field names (order-stable, de-duplicated,
    reserved base channel names dropped -- a graph author declaring "data" or
    "messages" in input_schema/output_schema just means that base channel,
    not a second one). Callers always pass already-extracted name lists (see
    _schema_field_names) -- input_schema and output_schema are both real
    JSON Schema objects, never a raw field name source themselves."""
    seen = set(_RESERVED_STATE_CHANNELS)
    names: list[str] = []
    for name_list in name_lists:
        for name in name_list or []:
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
    return names


def _schema_field_names(schema: dict[str, Any] | None) -> list[str]:
    """Declared field names from a real-JSON-Schema-shaped input_schema or
    output_schema (``{"type": "object", "properties": {...}, "required":
    [...]}``, the same convention node-level ``response_schema`` uses) --
    everything downstream (state channels, the compiled input/output
    TypedDicts, the seed node, the output projector) only ever needs the
    names, not the property definitions themselves."""
    return list((schema or {}).get("properties") or {})


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
        "data": Annotated[dict[str, Any], _merge_data],
        **dict.fromkeys(input_fields, Any),
    }
    return typed_dict_factory("RoutineInputState", annotations, total=False)


def _output_schema_type(output_fields: list[str], *, preserve_data: bool = False) -> Any:
    """``messages`` is always returned alongside whatever output_schema
    declares. Nested routine-as-subagent delegation (_build_routine_node /
    _build_object_agent_node) and the top-level CompiledSubAgent proxy
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
    if preserve_data:
        annotations["data"] = Annotated[dict[str, Any], _merge_data]
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


def _build_node_callable(
    node: RoutineNode,
    *,
    model: BaseChatModel,
    base_system_prompt: str | None,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    agent_background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    model_profile_resolver: Callable[[str], Awaitable[Any]] | None = None,
    tool_registry: Any = None,
    defs: dict[str, dict[str, Any]] | None = None,
    instruction_loader: Callable[[str], str] | None = None,
    policy_defaults: dict[str, Any] | None = None,
    review_evidence_source: ReviewEvidenceSource | None = None,
) -> Any:
    profile = _node_config(node).get("model_profile")
    if profile and node.kind in {"prompt", "classify"}:
        if model_profile_resolver is None:
            raise ValueError(f"node {node.id}: model profiles are unavailable")

        async def with_profile(state: RoutineState, runtime: Runtime | None = None):
            selected = await model_profile_resolver(profile)
            if node.kind == "prompt":
                call = _build_prompt_node(
                    node, model=selected, base_system_prompt=base_system_prompt, defs=defs
                )
                return await call(state, runtime)
            call = _build_classify_node(node, model=selected, defs=defs)
            return await call(state)

        return with_profile
    if node.kind == "prompt":
        return _build_prompt_node(
            node, model=model, base_system_prompt=base_system_prompt, defs=defs
        )
    if node.kind == "eval":
        return _build_eval_node(node)
    if node.kind == "reduce":
        return _build_reduce_node(node)
    if node.kind == "review_provenance":
        return _build_review_provenance_node(node, review_evidence_source)
    if node.kind == "condition":
        return _build_condition_node(node)
    if node.kind == "switch":
        return _build_switch_node(node)
    if node.kind == "split":
        return _build_split_node(node)
    if node.kind == "classify":
        return _build_classify_node(node, model=model, defs=defs)
    if node.kind == "agent":
        return _build_object_agent_node(
            node,
            object_agent_registry=agent_registry,
            defs=defs,
            instruction_loader=instruction_loader,
            background_runner=agent_background_runner,
        )
    if node.kind == "a2a":
        return _build_a2a_node(
            node,
            tool_registry=tool_registry,
            timeout=_node_timeout(_node_config(node), policy_defaults or {}),
        )
    if node.kind == "foreach":
        return _build_foreach_node(node, object_agent_registry=agent_registry)
    if node.kind == "human":
        return _build_human_node(node)
    if node.kind == "routine":
        return _build_routine_node(
            node,
            routine_registry=routine_registry,
            routine_runner=routine_runner,
        )
    if node.kind == "noop":
        return _build_noop_node(node)
    raise ValueError(f"unknown node kind: {node.kind}")


def _extract_token_usage(result: dict[str, Any] | None) -> dict[str, int] | None:
    """Sums ``usage_metadata`` off every ``AIMessage`` a node returned in
    ``messages`` -- the same uniform choke point as duration, so no node
    kind has to opt in individually. Every model-calling node builder
    already threads its raw response through ``messages`` (prompt/classify/
    agent/a2a/routine); kinds with no LLM call (eval/condition/switch/split/
    reduce/noop/human/foreach-send) correctly report no tokens by having no
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


def _instrument_node_callable(
    node_id: str,
    fn: Any,
    route_fn: Callable[[RoutineState], Any] | None = None,
    capture_output_key: str | None = None,
) -> Any:
    """Wrap a node's runnable to record wall-clock duration (and, when the
    node made a real LLM call, token usage, or wrote to `data`, that too)
    into run_meta, uniformly for every node kind -- independent of what the
    node itself returns, so it can never be skipped by omission the way a
    per-kind opt-in would be.
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

    ``route_fn``, present only for a node with a router or loop_guard edge,
    is the exact same decision function LangGraph itself calls moments
    later to pick the next node (see compile_routine_graph) -- re-run here,
    purely for recording, against the state this node's own update
    produces, so run_meta can say which branch was actually taken without a
    second, possibly-diverging implementation of the same decision. Errors
    evaluating it here are swallowed: a genuine routing failure still
    surfaces from LangGraph's own (real) call to the same function right
    after, unaffected by this side channel.
    """
    takes_runtime = len(inspect.signature(fn).parameters) >= 2

    async def wrapped(state: RoutineState, runtime: Any = None) -> dict[str, Any]:
        started = time.monotonic()
        started_at = utcnow_isoformat()
        # 1-based ordinal across every node execution so far this run --
        # the same simple choke point as duration/tokens below, not
        # LangGraph's own internal checkpoint step (not readable from
        # inside a node), but equivalent for ordering: it lets the UI place
        # this execution among every other node's, including across a
        # loop's repeated iterations, instead of only within this one
        # node's own entry list.
        step = sum(len(v) for v in (state.get("run_meta") or {}).values()) + 1
        try:
            result = await (fn(state, runtime) if takes_runtime else fn(state))
        except Exception as exc:
            duration_ms = (time.monotonic() - started) * 1000
            entry = {
                "started_at": started_at,
                "duration_ms": round(duration_ms, 2),
                "step": step,
                "error": str(exc) or type(exc).__name__,
            }
            invocation = getattr(exc, "routine_invocation", None)
            if invocation:
                entry["routine"] = dict(invocation)
            exc.routine_run_meta = (node_id, entry)  # type: ignore[attr-defined]
            raise
        duration_ms = (time.monotonic() - started) * 1000
        update = dict(result) if isinstance(result, dict) else {}
        entry: dict[str, Any] = {
            "started_at": started_at,
            "duration_ms": round(duration_ms, 2),
            "step": step,
        }
        invocation = update.pop("_routine_invocation", None)
        if invocation:
            entry["routine"] = invocation
        # This execution's own written fields (not the whole accumulated
        # `data` dict) -- `data` itself only ever holds the latest write per
        # key, so a node run multiple times (a loop body) would otherwise
        # have no way to recover what an earlier iteration actually wrote.
        new_data = update.get("data")
        if isinstance(new_data, dict) and new_data:
            entry["output"] = new_data
            if capture_output_key is not None and capture_output_key in new_data:
                update["node_outputs"] = {node_id: [new_data[capture_output_key]]}
        tokens = _extract_token_usage(result)
        if tokens is not None:
            entry["tokens"] = tokens
        if route_fn is not None:
            try:
                merged_data = {
                    **(state.get("data") or {}),
                    **(new_data if isinstance(new_data, dict) else {}),
                }
                entry["route"] = route_fn({**state, "data": merged_data})
            except Exception:  # nosec B110 -- see route_fn's docstring: the
                # real routing failure still surfaces from LangGraph's own
                # call to the same function moments later.
                pass
        update["run_meta"] = {node_id: [entry]}
        return update

    return wrapped


def compile_routine_graph(
    graph: RoutineGraph,
    *,
    model: BaseChatModel,
    agent_registry: dict[str, Any] | None = None,
    routine_registry: dict[str, Any] | None = None,
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    agent_background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    model_profile_resolver: Callable[[str], Awaitable[Any]] | None = None,
    tool_registry: Any = None,
    base_system_prompt: str | None = None,
    instruction_loader: Callable[[str], str] | None = None,
    checkpointer: Any | None = None,
    cache: Any | None = None,
    preserve_data: bool = False,
    execution_policy: PolicyValues | None = None,
    model_profiles: dict[str, Any] | None = None,
    review_evidence_source: ReviewEvidenceSource | None = None,
):
    """Compile a routine with LangGraph persistence, retries, context and fan-out.

    The graph document controls optional node retries/caches and recursion limits;
    callers provide the checkpointer and invocation context when resumability or
    human approval is required. ``routine_runner`` is the Postgres-backed,
    per-invocation resolver for nested routine nodes; ``routine_registry`` is
    retained for direct compiler callers and backwards compatibility.
    ``preserve_data`` keeps structured child state alongside a declared output
    projection for the execution adapter; direct callers retain the default
    projected output contract. ``review_evidence_source`` supplies the trusted
    card, reviewer-task and live PR reads for ``review_provenance`` nodes; without
    it those nodes always report ``source.unavailable``.
    """
    # Keep live registry identity even when it is initially empty.
    agent_registry = {} if agent_registry is None else agent_registry
    routine_registry = {} if routine_registry is None else routine_registry

    node_ids = {node.id for node in graph.nodes}
    if graph.entry not in node_ids:
        raise ValueError(f"entry references unknown node: {graph.entry}")

    input_fields = _schema_field_names(graph.input_schema)
    output_fields = _schema_field_names(graph.output_schema)
    nodes_by_id = {node.id: node for node in graph.nodes}
    capture_output_keys: dict[str, str] = {}
    for reduce_node in (node for node in graph.nodes if node.kind == "reduce"):
        source_id = _node_config(reduce_node)["source_node"]
        source_node = nodes_by_id.get(source_id)
        if source_node is None:
            raise ValueError(
                f"reduce node {reduce_node.id} references unknown source node: {source_id}"
            )
        capture_output_keys[source_id] = source_node.output_key or source_node.id

    builder = StateGraph(
        _extended_state_type(_extra_state_fields(input_fields, output_fields)),
        context_schema=RoutineContext,
        input_schema=_input_schema_type(input_fields),
        output_schema=_output_schema_type(output_fields, preserve_data=preserve_data),
    )
    policy = execution_policy or PolicyValues()
    policy_defaults = {"retry": policy.retry.model_dump(), "timeout": policy.attempt_timeout}
    cache_namespace = hashlib.sha256(
        json.dumps(
            [
                graph.model_dump(mode="json", by_alias=True),
                policy.model_dump(mode="json"),
                model_profiles,
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()

    effective_nodes = []
    for node in graph.nodes:
        config = _node_config(node)
        if (
            node.kind in {"prompt", "classify", "agent"}
            and not config.get("model_profile")
            and policy.default_model_profile
        ):
            config["model_profile"] = policy.default_model_profile
        if node.kind == "foreach":
            if config.get("max_items") is None and policy.default_fan_out_limit is not None:
                config["max_items"] = policy.default_fan_out_limit
            if config.get("mode") == "agent":
                config["max_concurrency"] = min(
                    config.get("max_concurrency") or policy.concurrency_limit,
                    policy.concurrency_limit,
                )
        if node.kind in CACHE_NODE_KINDS:
            node_cache = config.get("cache")
            if (
                (node_cache is None and policy.cache_enabled)
                or node_cache is True
                or node_cache == {}
            ):
                config["cache"] = (
                    {"ttl": policy.cache_ttl} if policy.cache_ttl is not None else True
                )
        if node.kind not in CACHE_NODE_KINDS and config.get("cache"):
            raise ValueError(f"node {node.id}: {node.kind} is not eligible for caching")
        effective_nodes.append(
            node.model_copy(update={"config": type(node.config).model_validate(config)})
        )
    graph = graph.model_copy(update={"nodes": effective_nodes})
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
    # Every path that would otherwise go straight to END routes through the
    # projector instead when output_schema is declared, so its declared
    # fields become real top-level channels before the graph actually ends
    # -- see _build_output_projector. Resolved before the node loop below
    # so a loop_guard's own route_fn (which needs it) can be built
    # alongside its node instead of on a second pass over the edges.
    terminal_target = END
    if output_fields:
        builder.add_node(ROUTINE_PROJECT_NODE_ID, _build_output_projector(output_fields))
        builder.add_edge(ROUTINE_PROJECT_NODE_ID, END)
        terminal_target = ROUTINE_PROJECT_NODE_ID

    # Error edges are wired as the node's error_handler below, not here: they
    # are not successors, so they neither occupy a node's outgoing slot nor
    # stop an otherwise-terminal node from reaching the end of the graph.
    flow_edges = [edge for edge in graph.edges if edge.is_flow]
    foreach_empty_targets = {
        edge.source_ids[0]: edge.target_ids[0]
        for edge in graph.edges
        if edge.level == "foreach_empty"
    }
    outgoing: dict[str, RoutineEdge] = {}
    for edge in flow_edges:
        for source in edge.source_ids:
            if source in outgoing:
                raise ValueError(f"node has more than one outgoing edge declaration: {source}")
            outgoing[source] = edge
    split_ids = {node.id for node in graph.nodes if node.kind == "split"}
    # The exact same routing decision function LangGraph itself calls to
    # pick the next node (built once, reused below and passed into
    # _instrument_node_callable) -- so a node's run_meta entry can record
    # which branch it actually took, without a second, possibly-diverging
    # implementation of the same decision. Only router (`route` level) and
    # loop_guard edges have a real decision worth recording; a plain edge
    # always goes the one place it's wired, a split fans out
    # unconditionally, and a foreach Send fans out per-item.
    route_fns: dict[str, Callable[[RoutineState], Any]] = {}
    for node_id, edge in outgoing.items():
        if edge.level == "route":
            route_fns[node_id] = (
                _build_split_router(edge) if node_id in split_ids else _build_router(edge)
            )
        elif edge.loop_guard is not None:
            route_fns[node_id] = _build_loop_guard(edge, terminal_target)

    builder.add_node(_SEED_NODE_ID, _seed_node(input_fields))
    builder.add_edge(START, _SEED_NODE_ID)
    builder.add_edge(_SEED_NODE_ID, graph.entry)

    for node in graph.nodes:
        config = _node_config(node)
        retry_config = {"retry": {**policy_defaults["retry"], **config.get("retry", {})}}
        recovery_target = error_routes.get(node.id)
        node_options: dict[str, Any] = {
            "retry_policy": _retry_policy(retry_config),
            "cache_policy": _cache_policy(config, cache_namespace),
            "defer": node.id in fan_in_targets,
            "timeout": TimeoutPolicy(
                run_timeout=_node_timeout(config, policy_defaults), idle_timeout=policy.idle_timeout
            )
            if policy.idle_timeout is not None
            else _node_timeout(config, policy_defaults),
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
                    routine_runner=routine_runner,
                    agent_background_runner=agent_background_runner,
                    model_profile_resolver=model_profile_resolver,
                    tool_registry=tool_registry,
                    defs=graph.defs,
                    instruction_loader=instruction_loader,
                    policy_defaults=policy_defaults,
                    review_evidence_source=review_evidence_source,
                ),
                route_fn=route_fns.get(node.id),
                capture_output_key=capture_output_keys.get(node.id),
            ),
            **node_options,
        )

    foreach_send_ids = {
        node.id
        for node in graph.nodes
        if node.kind == "foreach" and _node_config(node)["mode"] == "send"
    }
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
                route_fns[source],
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
                    next(node for node in graph.nodes if node.id == source),
                    targets[0],
                    foreach_empty_targets.get(source),
                ),
                path_map={
                    target: target
                    for target in {targets[0], foreach_empty_targets.get(source)}
                    if target is not None
                },
            )
            continue

        if edge.loop_guard is not None:
            false_target = edge.loop_guard.on_false or terminal_target
            builder.add_conditional_edges(
                source,
                route_fns[source],
                path_map={target: target for target in [*targets, false_target]},
            )
            continue

        for target in targets:
            builder.add_edge(source, target)

    if cache is None and any(
        _node_config(node).get("cache") not in (None, False) for node in graph.nodes
    ):
        cache = InMemoryCache()
    compiled = builder.compile(checkpointer=checkpointer, cache=cache)
    compiled = compiled.with_config(
        {"recursion_limit": policy.graph_step_limit, "max_concurrency": policy.concurrency_limit}
    )
    return compiled
