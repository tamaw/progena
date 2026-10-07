"""``CompiledSubAgent`` proxy so the Progenitor DeepAgent can delegate to a routine.

Wraps a routine id in a ``{"name", "description", "runnable"}`` spec shaped
for ``deepagents.middleware.subagents``, so ``task(subagent_type="<routine-id>", ...)``
resolves and runs the routine's compiled graph. Resolves the graph revision to
run in this order per invocation: the pin already present in the calling
thread's ``routine_revisions`` state (falling back to the latest captured
revision, then the live document, if that pin no longer exists); otherwise
-- the common case -- the live document from the state manager, tagged
(not sourced) with whatever revision_id in
:mod:`app.progenitor.routines.revisions` matches its exact content, if
any. Compiled graphs are cached by ``revision_id`` so a pinned thread never
recompiles or re-queries after its first call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, cast

from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.errors import NodeTimeoutError
from langgraph.types import Command

from ..utils import utcnow
from .approvals import (
    approval_decision_id,
    approval_identity,
    approval_lock,
    approval_resume_value,
    nested_approval_context,
    pending_approvals,
    validate_answer,
)
from .events import persist_run_event
from .factory import compile_routine_graph
from .graph import RoutineContext, validate_routine_graph_payload
from .revisions import RoutineRevisionReader

logger = logging.getLogger(__name__)

ROUTINE_REVISIONS_STATE_KEY = "routine_revisions"


async def resolve_routine_document(
    state_manager: Any, routine_id_or_name: str
) -> tuple[str, dict[str, Any]] | None:
    """Resolve a routine document by its stored id, or by its display name.

    Routine documents remain the source of truth.  The name fallback is used
    by nested routine nodes, whose schema stores ``routine_name`` rather than
    a separate foreign-key record.
    """
    requested = str(routine_id_or_name or "").strip()
    if not requested:
        return None
    direct = await state_manager.get_routine_graph(requested)
    if isinstance(direct, dict):
        canonical_id = str(direct.get("id") or requested).strip()
        return canonical_id, direct
    list_graphs = getattr(state_manager, "list_routine_graphs", None)
    if not callable(list_graphs):
        return None
    typed_list_graphs = cast(
        Callable[[], Awaitable[list[dict[str, Any]]]],
        list_graphs,
    )
    for graph_doc in await typed_list_graphs():
        if not isinstance(graph_doc, dict):
            continue
        graph_id = str(graph_doc.get("id") or "").strip()
        graph_name = str(graph_doc.get("name") or "").strip()
        if requested in {graph_id, graph_name} and graph_id:
            return graph_id, graph_doc
    return None


def create_routine_executor(
    *,
    routine_id: str,
    model: BaseChatModel,
    state_manager: Any,
    revision_reader: RoutineRevisionReader,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    agent_background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    tool_registry: Any = None,
    checkpointer: Any | None = None,
    context: dict[str, Any] | None = None,
) -> RunnableLambda:
    """Execute a routine while preserving structured state for nested callers."""

    cache: dict[str, Any] = {}

    async def _run(state: dict) -> dict:
        pins = state.get(ROUTINE_REVISIONS_STATE_KEY) or {}
        pinned = pins.get(routine_id)

        revision_id: str | None = None
        graph_doc: dict[str, Any] | None = None

        if pinned and pinned in cache:
            revision_id = pinned
        elif pinned:
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
                row = await revision_reader.fetch_latest(routine_id)
                if row is not None:
                    revision_id, graph_doc = row
                if revision_id is None and graph_doc is None:
                    graph_doc = await state_manager.get_routine_graph(routine_id)
        else:
            # Unpinned: run against the LIVE document, not whichever
            # revision `routine_graph_revisions` happens to have captured
            # most recently. Revision capture is a content-hash-
            # deduplicated audit log (`ON CONFLICT (routine_id,
            # revision_id) DO NOTHING` -- see document_store.py's trigger):
            # if the live content ever reverts to any exact byte-identical
            # prior state (e.g. via the Revisions panel's own Restore
            # action, or simply editing a field back to its original
            # value), that revert produces NO new row and leaves the old
            # row's `captured_at` untouched -- so `fetch_latest`
            # (`ORDER BY captured_at DESC`) can return a revision that is
            # no longer what is actually live, and a run would silently
            # compile and execute against superseded content. The live
            # document is always the source of truth for an unpinned run;
            # `fetch_revision_id_for_content` below only *tags* the run
            # (thread_id / ROUTINE_REVISIONS_STATE_KEY) with whichever
            # revision_id, if any, matches this exact content -- for the
            # Runs panel and the canvas's run-staleness diffing -- it is
            # never used to decide what gets compiled.
            graph_doc = await state_manager.get_routine_graph(routine_id)
            if graph_doc is not None:
                revision_id = await revision_reader.fetch_revision_id_for_content(
                    routine_id, graph_doc
                )

        if revision_id is None and graph_doc is None:
            raise KeyError(f"Routine '{routine_id}' has no stored graph.")

        compiled: Any = None
        run_config: RunnableConfig | None = None
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
                    routine_runner=routine_runner,
                    agent_background_runner=agent_background_runner,
                    tool_registry=tool_registry,
                    base_system_prompt=None,
                    checkpointer=checkpointer,
                    preserve_data=True,
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
            #
            # An interactive (node-level step/edit) caller instead passes a
            # stable `_interactive_session_id` via state, kept constant across
            # repeated clicks within one editor session so each click resumes
            # the SAME thread's checkpoint history rather than starting over.
            # It only stays valid within one revision, by design: revision_id
            # is baked into the thread_id, so a save (new revision) naturally
            # produces a new thread with no history -- carrying prior state
            # forward across a revision change is the checkpoint-copy-forward
            # seeding this primitive does not yet do (tracked separately).
            # `_interactive_target_node` interrupts AFTER that node runs, not
            # before -- "Run to here" is meant to execute the clicked node and
            # show its result, then pause so the next click can pick up where
            # this one left off.
            session_id = state.get("_interactive_session_id")
            target_node = state.get("_interactive_target_node")
            run_thread_id = (
                f"routine:{routine_id}:{revision_id or 'unpinned'}:{session_id}"
                if session_id
                else f"routine:{routine_id}:{revision_id or 'unpinned'}:{uuid.uuid4().hex}"
            )
            run_config = {"configurable": {"thread_id": run_thread_id}}
            execution = state["_routine_execution"]
            execution.update(thread_id=run_thread_id, revision_id=revision_id)
            execution["name"] = graph_doc.get("name", routine_id) if graph_doc else routine_id
            await persist_run_event(state_manager, execution, "started")
            # A stable thread already carrying checkpoint history must be
            # resumed (input=None) rather than re-invoked with fresh input --
            # LangGraph treats non-None input as a brand-new run regardless
            # of existing history, which would silently discard everything
            # already checkpointed under this thread_id.
            resume_existing = False
            run_input: Any = None
            run_context = nested_approval_context(
                [], {**(context or {}), "request_id": run_thread_id}
            )
            if session_id and checkpointer is not None:
                existing_state = await compiled.aget_state(run_config)
                resume_existing = bool(existing_state and existing_state.values)
                run_context = nested_approval_context(existing_state.interrupts, run_context)
                resume = state.get("_routine_resume")
                if resume and any(
                    approval_decision_id(item) == resume["interrupt_id"]
                    and approval_identity(item.value) == approval_identity(resume["approval"])
                    for item in existing_state.interrupts
                ):
                    approval = validate_answer(
                        pending_approvals(existing_state.interrupts),
                        resume["answer"],
                        resume["interrupt_id"],
                    )
                    run_input = Command(
                        resume={
                            approval["resume_interrupt_id"]: approval_resume_value(
                                existing_state.interrupts,
                                approval["resume_interrupt_id"],
                                resume["answer"],
                            )
                        }
                    )
            parameters = state.get("parameters")
            seed_input: dict[str, Any] = {
                "messages": state.get("messages") or [],
                "data": dict(state.get("data") or {}),
            }
            seed_input.update(
                {
                    key: value
                    for key, value in seed_input["data"].items()
                    if key not in {"messages", "data", "run_meta"}
                }
            )
            if isinstance(parameters, dict):
                # Top-level keys, not nested under "parameters": the seed
                # node (factory.py's _seed_node) reads state.get(field) for
                # each name declared in input_schema.properties, matching
                # how the compiled graph's own input_schema TypedDict
                # declares them (see _input_schema_type).
                seed_input.update(parameters)
            result = await compiled.ainvoke(
                run_input if resume_existing else seed_input,
                config=run_config,
                context=run_context,
                interrupt_after=[target_node] if target_node else None,
            )
        except Exception as exc:
            # See factory.py's _instrument_node_callable: a node that raises
            # never gets to *return* its own run_meta entry, so LangGraph
            # never checkpoints one for it. Stash it onto the checkpoint
            # manually here -- the only place with both `compiled` and this
            # run's `run_config` in scope -- before converting the failure
            # into the same chat message as always; a failure persisting
            # this entry is logged and swallowed rather than replacing the
            # real failure message below.
            node_meta = getattr(exc, "routine_run_meta", None)
            timed_out = isinstance(exc, NodeTimeoutError) and checkpointer is not None
            if (
                (node_meta is not None or timed_out)
                and compiled is not None
                and run_config is not None
            ):
                if node_meta is not None:
                    failed_node_id = node_meta[0]
                else:
                    assert isinstance(exc, NodeTimeoutError)
                    failed_node_id = exc.node
                try:
                    if node_meta is not None:
                        _, entry = node_meta
                    else:
                        assert isinstance(exc, NodeTimeoutError)
                        # LangGraph's watchdog cancels the node and raises this
                        # outside our wrapper, so it has no routine_run_meta.
                        # Its elapsed time describes the final attempt only.
                        started_at = (utcnow() - timedelta(seconds=exc.elapsed)).isoformat()
                        snapshot = await compiled.aget_state(run_config)
                        history = snapshot.values.get("run_meta") or {}
                        entry = {
                            "started_at": started_at,
                            "duration_ms": round(exc.elapsed * 1000, 2),
                            "step": sum(len(records) for records in history.values()) + 1,
                            "error": str(exc),
                        }
                    await compiled.aupdate_state(
                        run_config, {"run_meta": {failed_node_id: [entry]}}
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist run_meta for failed node %r",
                        failed_node_id,
                        exc_info=True,
                    )
            raise

        update: dict[str, Any] = dict(result)
        update["_routine_thread_id"] = run_thread_id
        if checkpointer is not None:
            snapshot = await compiled.aget_state(run_config)
            update["_routine_paused"] = bool(snapshot.next or snapshot.interrupts)
        else:
            update["_routine_paused"] = bool(result.get("__interrupt__"))
        if revision_id is not None:
            update[ROUTINE_REVISIONS_STATE_KEY] = {routine_id: revision_id}
        return update

    async def _execute(state: dict) -> dict:
        session_id = state.get("_interactive_session_id") or uuid.uuid4().hex
        execution = {
            "agent_id": routine_id,
            "session_id": session_id,
            "request_id": state.get("_routine_request_id") or str(uuid.uuid4()),
        }
        if state.get("_routine_parent"):
            execution["parent"] = dict(state["_routine_parent"])
        state = {**state, "_interactive_session_id": session_id, "_routine_execution": execution}
        # These invocations own independent checkpoint threads. Older
        # LangGraph releases merge the caller's task/namespace into an
        # explicitly supplied thread_id, making child interrupts bubble out
        # before we can record invocation provenance and pause the parent.
        # Preserve tracing and user configuration, but detach graph controls.
        inherited = var_child_runnable_config.get() or {}
        detached = cast(
            RunnableConfig,
            {
                **inherited,
                "configurable": {
                    key: value
                    for key, value in inherited.get("configurable", {}).items()
                    if not key.startswith("__pregel_")
                    and key not in {"checkpoint_ns", "checkpoint_id", "checkpoint_map"}
                },
            },
        )
        token = var_child_runnable_config.set(detached)
        try:
            result = await _run(state)
        except Exception as exc:
            cast(Any, exc).routine_invocation = dict(execution)
            await persist_run_event(state_manager, {**execution, "error": str(exc)}, "failed")
            cast(Any, exc).routine_lifecycle_reported = True
            raise
        finally:
            var_child_runnable_config.reset(token)
        await persist_run_event(
            state_manager, execution, "paused" if result.get("_routine_paused") else "completed"
        )
        # Execution provenance, not a dependency pin. The caller checkpoints
        # this alongside that node's output; the next call still resolves live.
        result["_routine_invocation"] = dict(execution)
        return result

    return RunnableLambda(_execute)


def create_routine_subagent(
    *,
    routine_id: str,
    description: str,
    model: BaseChatModel,
    state_manager: Any,
    revision_reader: RoutineRevisionReader,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any],
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    agent_background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    tool_registry: Any = None,
    checkpointer: Any | None = None,
) -> CompiledSubAgent:
    """Expose execution as a conversational task without leaking child state."""
    executor = create_routine_executor(
        routine_id=routine_id,
        model=model,
        state_manager=state_manager,
        revision_reader=revision_reader,
        agent_registry=agent_registry,
        routine_registry=routine_registry,
        routine_runner=routine_runner,
        agent_background_runner=agent_background_runner,
        tool_registry=tool_registry,
        checkpointer=checkpointer,
    )

    async def _run(state: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await executor.ainvoke(state)
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
                            f"Resume it with resume_routine(thread_id={result['_routine_thread_id']!r}, answer=<your answer>)."
                        )
                    )
                ]
            }

        update: dict[str, Any] = {"messages": result.get("messages") or []}
        if ROUTINE_REVISIONS_STATE_KEY in result:
            update[ROUTINE_REVISIONS_STATE_KEY] = result[ROUTINE_REVISIONS_STATE_KEY]
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
    *, thread_id: str, answer: Any, database_url: str | None = None, **kwargs: Any
) -> dict[str, Any]:
    """Answer one still-pending interrupt, shared by UI and Progenitor chat."""
    async with approval_lock(thread_id, database_url, wait=True):
        return await _resume_routine_thread(thread_id=thread_id, answer=answer, **kwargs)


async def _resume_routine_thread(
    *,
    thread_id: str,
    answer: Any,
    model: BaseChatModel,
    state_manager: Any,
    revision_reader: RoutineRevisionReader,
    agent_registry: dict[str, Any],
    routine_registry: dict[str, Any] | None = None,
    routine_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    agent_background_runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    tool_registry: Any = None,
    checkpointer: Any | None = None,
    interrupt_id: str | None = None,
    request_id: str | None = None,
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
        routine_runner=routine_runner,
        agent_background_runner=agent_background_runner,
        tool_registry=tool_registry,
        base_system_prompt=None,
        checkpointer=checkpointer,
    )
    run_config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    checkpoint = await compiled.aget_state(run_config)
    approval = validate_answer(pending_approvals(checkpoint.interrupts), answer, interrupt_id)
    get_decision = getattr(state_manager, "get_routine_approval_decision", None)
    if callable(get_decision):
        read_decision = cast(Callable[..., Awaitable[Any]], get_decision)
        decision = await read_decision(thread_id, approval["interrupt_id"])
        if decision and decision.get("request_id") != request_id:
            raise ValueError("An answer for this approval has already been submitted")
    saved_context = (checkpoint.values.get("data") or {}).get("context") or {}
    execution = {
        "agent_id": routine_id,
        "revision_id": revision_id,
        "thread_id": thread_id,
        "session_id": thread_id.split(":")[-1],
        "request_id": request_id or str(uuid.uuid4()),
    }
    await persist_run_event(state_manager, execution, "started")
    try:
        result = await compiled.ainvoke(
            Command(
                resume={
                    approval["resume_interrupt_id"]: approval_resume_value(
                        checkpoint.interrupts, approval["resume_interrupt_id"], answer
                    )
                }
            ),
            config=run_config,
            context=cast(
                RoutineContext,
                nested_approval_context(
                    checkpoint.interrupts, {**saved_context, "request_id": thread_id}
                ),
            ),
        )
    except Exception as exc:
        await persist_run_event(state_manager, {**execution, "error": str(exc)}, "failed")
        cast(Any, exc).routine_lifecycle_reported = True
        raise
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    await persist_run_event(state_manager, execution, "paused" if interrupts else "completed")
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
