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
import uuid
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
    checkpointer: Any | None = None,
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
                    checkpointer=checkpointer,
                )
                if revision_id is not None:
                    cache[revision_id] = compiled
            # One thread_id per top-level run, not per node/call -- lets
            # "every checkpoint row for this run" be found later by a plain
            # `thread_id LIKE 'routine:<routine_id>:%'` scan (see the Runs
            # panel), and folds in the revision actually executed so a run
            # against a stale pinned revision is distinguishable from one
            # against latest. Nested `agent`/`routine` node calls inside this
            # run are NOT given their own prefix -- they're steps of this run,
            # not separate runs.
            run_thread_id = f"routine:{routine_id}:{revision_id or 'unpinned'}:{uuid.uuid4().hex}"
            result = await compiled.ainvoke(
                {"messages": state.get("messages") or []},
                config={"configurable": {"thread_id": run_thread_id}},
            )
        except (ValueError, KeyError, NotImplementedError) as exc:
            return {"messages": [AIMessage(content=f"Routine '{routine_id}' failed: {exc}")]}

        update: dict[str, Any] = {"messages": result.get("messages") or []}
        if revision_id is not None:
            update[ROUTINE_REVISIONS_STATE_KEY] = {routine_id: revision_id}
        return update

    return {"name": routine_id, "description": description, "runnable": RunnableLambda(_run)}
