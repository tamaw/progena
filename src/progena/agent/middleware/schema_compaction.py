"""Tool-schema compaction middleware.

Strips provider-irrelevant JSON-schema metadata before each model call to
reduce token prefill.
"""

import copy
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from pydantic import ConfigDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool-schema compaction (per-turn LLM prefill reduction)
#
# LangChain serializes args_schema.model_json_schema() into model requests.
# Strip metadata but preserve Pydantic's standard anyOf nullable unions: the
# QuickJS tool-reference renderer consumes these schemas before provider binding
# and does not support the alternative JSON Schema "type": [T, "null"] form.
# A per-model json_schema_extra hook leaves input validation untouched.
_SCHEMA_NOISE_KEYS = frozenset({"title", "$schema", "definitions"})


def _compact_json_schema(schema: Any) -> None:
    """Strip provider-irrelevant JSON-schema metadata in place."""
    if isinstance(schema, dict):
        # Drop provider-irrelevant metadata, retaining validation keywords.
        for k in tuple(schema.keys()):
            if k in _SCHEMA_NOISE_KEYS:
                del schema[k]
        # Recurse into all nested dicts/lists (properties, $defs, items, ...).
        for v in list(schema.values()):
            if isinstance(v, dict):
                _compact_json_schema(v)
            elif isinstance(v, list):
                for item in v:
                    _compact_json_schema(item)
    elif isinstance(schema, list):
        for item in schema:
            _compact_json_schema(item)


def _compact_args_model(
    args_model: Any,
    cache: dict[int, Any] | None = None,
) -> Any:
    """Return a compacted args-schema equivalent to ``args_model``.

    Handles the two shapes ``tool.args_schema`` takes across Progena's tool
    sources:

    - **Pydantic model class** (system tools, DeepAgent built-ins): returns a
      per-class subclass whose ``model_json_schema()`` is compacted via
      ``ConfigDict(json_schema_extra=...)``. Idempotent via the
      ``__progena_schema_compacted__`` class marker.
    - **Raw JSON-Schema dict** (``langchain_mcp_adapters`` wraps every MCP
      tool's ``args_schema`` as the server's ``inputSchema`` dict verbatim --
      MCP tools have no Python model class to subclass): returns a
      deep-copied, compacted dict. A bare dict can't carry an idempotency
      marker attribute, so idempotency instead relies entirely on ``cache``:
      the compacted dict is also registered under its own id, so a later call
      re-checking an already-compacted dict is a cache hit and returns the
      same object unchanged.

    ``cache`` memoises results by original (or already-compacted) object id
    so per-turn middleware does not redo this work for the same tool twice.
    """
    if args_model is None:
        return args_model
    if isinstance(args_model, dict):
        key = id(args_model)
        if cache is not None:
            cached = cache.get(key)
            if cached is not None:
                return cached
        compacted_schema = copy.deepcopy(args_model)
        _compact_json_schema(compacted_schema)
        if cache is not None:
            cache[key] = compacted_schema
            # Register the compacted dict under its own id too, so a future
            # call passed this same (already-compacted) dict short-circuits
            # as a cache hit instead of re-compacting an ever-growing chain.
            cache[id(compacted_schema)] = compacted_schema
        return compacted_schema
    if getattr(args_model, "__progena_schema_compacted__", False):
        return args_model
    key = id(args_model)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    subclass = type(args_model)(  # noqa: PLE0241 - intentionally dynamic for schema compaction
        str(getattr(args_model, "__name__", "Args")) + "_CompactSchema",
        (args_model,),
        {
            "model_config": ConfigDict(json_schema_extra=_compact_json_schema),
            "__progena_schema_compacted__": True,
        },
    )
    if cache is not None:
        cache[key] = subclass
    return subclass


class SchemaCompactionMiddleware(AgentMiddleware):
    """Strip provider-irrelevant tool-schema noise before each model call.

    Hooks ``awrap_model_call`` (and its sync twin) to replace every tool's
    ``args_schema`` with a memoised compacted subclass. The strip runs at most
    once per original Pydantic model: subsequent turns see the cached subclass
    via the ``__progena_schema_compacted__`` marker and short-circuit, so the
    per-turn cost collapses to a flag check (microseconds). The mutated
    ``args_schema`` lives on the tool instance for the agent's lifetime -- the
    built-in DeepAgent tools, Progena system tools, and dynamically-merged
    MCP tools all flow through this same path because they all land in
    ``request.tools`` before ``bind_tools`` is invoked.
    """

    def __init__(self) -> None:
        self._compacted_args_models: dict[int, Any] = {}

    def _compact_tool_in_place(self, tool: Any) -> None:
        args_model = getattr(tool, "args_schema", None)
        if args_model is None:
            return
        if getattr(args_model, "__progena_schema_compacted__", False):
            return
        compacted = _compact_args_model(args_model, self._compacted_args_models)
        if compacted is args_model:
            return
        try:
            tool.args_schema = compacted
        except Exception:
            # Frozen pydantic model: bypass the guarded __setattr__.
            object.__setattr__(tool, "args_schema", compacted)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Any],
    ) -> Any:
        for tool in request.tools or []:
            self._compact_tool_in_place(tool)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        for tool in request.tools or []:
            self._compact_tool_in_place(tool)
        return await handler(request)
