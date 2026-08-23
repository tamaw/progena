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
from langgraph.types import Command

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

        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        if interrupts:
            # A human node paused execution -- it's checkpointed and
            # resumable under run_thread_id, not a failure. Surface the
            # question plus the exact thread_id so the caller can answer it
            # via the resume_routine tool (see resume_routine_thread below);
            # silently returning empty here would look like a no-op success.
            payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
            question = payload.get("message") if isinstance(payload, dict) else str(payload)
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"Routine '{routine_id}' is waiting for a human answer: {question}\n"
                            f"Resume it with resume_routine(thread_id={run_thread_id!r}, answer=<your answer>)."
                        )
                    )
                ]
            }

        update: dict[str, Any] = {"messages": result.get("messages") or []}
        if revision_id is not None:
            update[ROUTINE_REVISIONS_STATE_KEY] = {routine_id: revision_id}
        return update

    return {"name": routine_id, "description": description, "runnable": RunnableLambda(_run)}


def _parse_routine_thread_id(thread_id: str) -> tuple[str, str | None]:
    """Split a ``routine:<routine_id>:<revision_id>:<uuid>`` thread_id.

    Returns ``(routine_id, revision_id)``; ``revision_id`` is ``None`` when
    the run was against the live (unpinned) document (see the ``'unpinned'``
    sentinel written by ``create_routine_subagent``'s ``_run``).
    """
    parts = thread_id.split(":")
    if len(parts) != 4 or parts[0] != "routine":
        raise ValueError(f"not a routine run thread_id: {thread_id!r}")
    _, routine_id, revision_id, _run_uuid = parts
    return routine_id, (None if revision_id == "unpinned" else revision_id)


async def resume_routine_thread(
    *,
    thread_id: str,
    answer: Any,
    model: BaseChatModel,
    state_manager: Any,
    revision_reader: RoutineRevisionReader,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    tool_registry: Any = None,
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Resume a routine run paused at a human node, given its exact thread_id.

    Shared primitive: this is also what the interactive node-level run/edit
    feature resumes through, since both cases are "continue this checkpointed
    thread with a `Command(resume=...)`" -- only how the answer/edit gets
    produced differs. Recompiles the routine fresh (resume is rare; no need
    to share create_routine_subagent's per-invocation compile cache).
    """
    routine_id, revision_id = _parse_routine_thread_id(thread_id)
    graph_doc: dict[str, Any] | None = None
    if revision_id is not None:
        graph_doc = await revision_reader.fetch(routine_id, revision_id)
    if graph_doc is None:
        graph_doc = await state_manager.get_routine_graph(routine_id)
    if graph_doc is None:
        raise ValueError(f"routine {routine_id!r} has no stored graph to resume against")

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
    result = await compiled.ainvoke(
        Command(resume=answer),
        config={"configurable": {"thread_id": thread_id}},
    )
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        question = payload.get("message") if isinstance(payload, dict) else str(payload)
        return {
            "status": "interrupted",
            "thread_id": thread_id,
            "question": question,
        }
    return {
        "status": "completed",
        "thread_id": thread_id,
        "messages": [str(getattr(m, "content", m)) for m in (result.get("messages") or [])],
    }
