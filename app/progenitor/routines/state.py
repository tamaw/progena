"""Shared routine state channels and node state/configuration access."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from .graph import RoutineNode

_DEFS_REF_PREFIX = "#/$defs/"


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
    # node kind by factory._instrument_node_callable -- independent of what
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
