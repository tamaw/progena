"""``CompiledSubAgent`` proxy so the Progenitor DeepAgent can delegate to a routine.

Wraps a routine id in a ``{"name", "description", "runnable"}`` spec shaped
for ``deepagents.middleware.subagents``, so ``task(subagent_type="<routine-id>", ...)``
resolves and runs the routine's compiled graph. Resolves the graph revision to
run in this order per invocation: the pin already present in the calling
thread's ``routine_revisions`` state, else the latest revision from
:mod:`app.progenitor.routines.revisions`, else the live (unpinned) document
from the state manager. Compiled graphs are cached by ``revision_id`` so a
pinned thread never recompiles or re-queries after its first call.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from .factory import compile_routine_graph
from .graph import validate_routine_graph_payload
from .revisions import RoutineRevisionReader

logger = logging.getLogger(__name__)

ROUTINE_REVISIONS_STATE_KEY = "routine_revisions"


def create_routine_subagent(
    *,
    routine_id: str,
    description: str,
    model: BaseChatModel,
    state_manager: Any,
    revision_reader: RoutineRevisionReader,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    tool_registry: Any = None,
) -> CompiledSubAgent:
    """Build a ``CompiledSubAgent``-shaped dict that proxies to routine ``routine_id``."""

    cache: dict[str, Any] = {}

    async def _run(state: dict) -> dict:
        pins = state.get(ROUTINE_REVISIONS_STATE_KEY) or {}
        pinned = pins.get(routine_id)

        revision_id: str | None = None
        graph_doc: dict[str, Any] | None = None

        if pinned and pinned in cache:
            revision_id = pinned
        else:
            if pinned:
                fetched = await revision_reader.fetch(routine_id, pinned)
                if fetched is not None:
                    revision_id = pinned
                    graph_doc = fetched
                else:
                    logger.warning(
                        "Routine %r pinned revision %r not found; falling back to latest",
                        routine_id,
                        pinned,
                    )
            if revision_id is None:
                row = await revision_reader.fetch_latest(routine_id)
                if row is not None:
                    revision_id, graph_doc = row
            if revision_id is None and graph_doc is None:
                graph_doc = await state_manager.get_routine_graph(routine_id)

        if revision_id is None and graph_doc is None:
            return {"messages": [AIMessage(content=f"Routine '{routine_id}' has no stored graph.")]}

        try:
            if revision_id is not None and revision_id in cache:
                compiled = cache[revision_id]
            else:
                assert graph_doc is not None  # guaranteed by the resolution above
                graph = validate_routine_graph_payload(graph_doc)
                compiled = compile_routine_graph(
                    graph,
                    model=model,
                    agent_registry=agent_registry,
                    routine_registry=routine_registry,
                    tool_registry=tool_registry,
                    base_system_prompt=None,
                )
                if revision_id is not None:
                    cache[revision_id] = compiled
            result = await compiled.ainvoke({"messages": state.get("messages") or []})
        except (ValueError, KeyError, NotImplementedError) as exc:
            return {"messages": [AIMessage(content=f"Routine '{routine_id}' failed: {exc}")]}

        update: dict[str, Any] = {"messages": result.get("messages") or []}
        if revision_id is not None:
            update[ROUTINE_REVISIONS_STATE_KEY] = {routine_id: revision_id}
        return update

    return {"name": routine_id, "description": description, "runnable": RunnableLambda(_run)}
