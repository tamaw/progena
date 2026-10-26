"""``CompiledSubAgent`` proxy so the Progena DeepAgent can delegate to a routine.

Wraps a routine id in a ``{"name", "description", "runnable"}`` spec shaped
for ``deepagents.middleware.subagents``, so ``task(subagent_type="<routine-id>", ...)``
resolves and runs the routine's compiled graph. Resolves the graph revision to
run in this order per invocation: the pin already present in the calling
thread's ``routine_revisions`` state (falling back to the latest captured
revision, then the live document, if that pin no longer exists); otherwise
-- the common case -- the live document from the state manager, tagged
(not sourced) with whatever revision_id in
:mod:`src.progena.routines.revisions` matches its exact content, if
any. Compiled graph identity includes the graph document, its execution-policy
snapshot and model configuration used by caching. New runs resolve live policy
documents; continuations use the policy snapshot in their checkpoint.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, Literal, cast

from deepagents.middleware.subagents import CompiledSubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.cache.memory import InMemoryCache
from langgraph.errors import NodeTimeoutError
from langgraph.types import Command

from src.progena.llm.structured import validate_against_schema
from src.progena.settings.policies import PolicyValues, resolve_execution_policy

from ..instructions.templates import instruction_relative_path
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
from .controls import (
    RoutineRunCancelled,
    RoutineRunControlExit,
    RoutineRunController,
    current_routine_execution_scope,
    routine_session_from_thread,
)
from .events import persist_run_event, routine_run_output
from .factory import compile_routine_graph
from .graph import RoutineContext, validate_routine_graph_payload
from .revisions import RoutineRevisionReader

logger = logging.getLogger(__name__)

ROUTINE_REVISIONS_STATE_KEY = "routine_revisions"
# Private input key: the (revision_id, graph) a caller already resolved for this invocation.
ROUTINE_GRAPH_SNAPSHOT_STATE_KEY = "_routine_graph_snapshot"
_RUNTIME_NODE_CACHE = InMemoryCache()


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


async def _routine_instruction_loader(
    state_manager: Any,
) -> tuple[Callable[[str], str], dict[str, str]]:
    """Snapshot instructions once per routine compilation, matching workflow reads."""
    instructions = dict(await state_manager.list_content_documents("instruction"))

    def load(path: str) -> str:
        relative_path = instruction_relative_path(path)
        text = instructions.get(relative_path)
        if text is None:
            raise ValueError(f"instruction '{path}' not found")
        return text

    return load, instructions


async def resolve_routine_graph(
    routine_id: str,
    state: Mapping[str, Any],
    *,
    revision_reader: RoutineRevisionReader,
    state_manager: Any,
) -> tuple[str | None, dict[str, Any] | None]:
    """The ``(revision_id, graph document)`` one routine invocation runs against."""

    pins = state.get(ROUTINE_REVISIONS_STATE_KEY) or {}
    pinned = pins.get(routine_id)

    revision_id: str | None = None
    graph_doc: dict[str, Any] | None = None

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
            revision_id = await revision_reader.fetch_revision_id_for_content(routine_id, graph_doc)

    return revision_id, graph_doc


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
    model_profile_resolver: Callable[[str], Awaitable[Any]] | None = None,
    tool_registry: Any = None,
    filesystem_backend: Any | None = None,
    checkpointer: Any | None = None,
    context: dict[str, Any] | None = None,
    routine_controls: RoutineRunController | None = None,
) -> RunnableLambda:
    """Execute a routine while preserving structured state for nested callers."""

    cache: dict[str, Any] = {}
    routine_controls = routine_controls or RoutineRunController(state_manager)

    async def _run(state: dict) -> dict:
        resume_thread_id = str(state.get("_routine_resume_thread_id") or "").strip() or None
        requested_session_id = str(state.get("_interactive_session_id") or "").strip() or None
        if resume_thread_id:
            resumed_routine_id, _resume_revision_id = _parse_routine_thread_id(resume_thread_id)
            if resumed_routine_id != routine_id:
                raise ValueError("resume thread does not belong to this routine")
            initial_session_id = routine_session_from_thread(resume_thread_id, routine_id)
        else:
            initial_session_id = requested_session_id
        if initial_session_id:
            await routine_controls.assert_not_cancelled(routine_id, initial_session_id)
        if resume_thread_id:
            resumed_routine_id, revision_id = _parse_routine_thread_id(resume_thread_id)
            if resumed_routine_id != routine_id:
                raise ValueError("resume thread does not belong to this routine")
            if revision_id is None:
                raise ValueError("stopped routine has no pinned graph revision")
            graph_doc = await revision_reader.fetch(routine_id, revision_id)
            if graph_doc is None:
                raise ValueError(
                    f"pinned revision {revision_id!r} for stopped routine is unavailable"
                )
        else:
            snapshot = state.get(ROUTINE_GRAPH_SNAPSHOT_STATE_KEY)
            if snapshot is not None:
                revision_id, graph_doc = snapshot
            else:
                revision_id, graph_doc = await resolve_routine_graph(
                    routine_id, state, revision_reader=revision_reader, state_manager=state_manager
                )

        if revision_id is None and graph_doc is None:
            raise KeyError(f"Routine '{routine_id}' has no stored graph.")

        assert graph_doc is not None
        selected_graph = validate_routine_graph_payload(graph_doc)
        if resume_thread_id:
            session_id = routine_session_from_thread(resume_thread_id, routine_id)
            if requested_session_id and requested_session_id != session_id:
                raise ValueError("resume session does not match its stopped checkpoint")
            if checkpointer is None:
                raise ValueError("stopped routine checkpoint is unavailable")
            run_thread_id = resume_thread_id
        else:
            session_id = requested_session_id or uuid.uuid4().hex
            run_thread_id = f"routine:{routine_id}:{revision_id or 'unpinned'}:{session_id}"
        run_config: RunnableConfig = {"configurable": {"thread_id": run_thread_id}}
        # This guard is intentionally before lifecycle ``started``: a cancelled
        # logical session must not look like a fresh attempted continuation.
        await routine_controls.assert_not_cancelled(routine_id, session_id)

        execution_policy = None
        if (requested_session_id or resume_thread_id) and checkpointer is not None:
            previous = await checkpointer.aget_tuple(run_config)
            recorded = (
                (previous.checkpoint.get("channel_values", {}).get("data") or {}).get(
                    "_execution_policy"
                )
                if previous is not None
                else None
            )
            if resume_thread_id and not isinstance(recorded, dict):
                raise ValueError("stopped routine has no recorded execution policy")
            if isinstance(recorded, dict):
                execution_policy = recorded
        if execution_policy is None:
            execution_policy = await resolve_execution_policy(
                state_manager, selected_graph.policy_id, revision_reader
            )
        policy_values = PolicyValues.model_validate(execution_policy["values"])
        model_profiles = (
            await state_manager.get_model_profiles_config()
            if (
                policy_values.cache_enabled
                or any(
                    node.config.model_dump().get("cache") not in (None, False)
                    for node in selected_graph.nodes
                )
            )
            else None
        )
        instruction_loader = None
        instruction_documents: dict[str, str] | None = None
        if any(
            node.kind == "agent" and node.config.instruction is not None
            for node in selected_graph.nodes
        ):
            instruction_loader, instruction_documents = await _routine_instruction_loader(
                state_manager
            )
        compile_key = json.dumps(
            [revision_id, graph_doc, execution_policy, model_profiles, instruction_documents],
            sort_keys=True,
        )
        compiled: Any = None
        try:
            if compile_key in cache:
                compiled = cache[compile_key]
            else:
                graph = validate_routine_graph_payload(graph_doc)
                compiled = compile_routine_graph(
                    graph,
                    model=model,
                    agent_registry=agent_registry,
                    routine_registry=routine_registry,
                    routine_runner=routine_runner,
                    agent_background_runner=agent_background_runner,
                    model_profile_resolver=model_profile_resolver,
                    tool_registry=tool_registry,
                    filesystem_backend=filesystem_backend,
                    base_system_prompt=None,
                    instruction_loader=instruction_loader,
                    checkpointer=checkpointer,
                    preserve_data=True,
                    execution_policy=policy_values,
                    cache=_RUNTIME_NODE_CACHE,
                    model_profiles=model_profiles,
                )
                cache[compile_key] = compiled
            execution = state["_routine_execution"]
            execution.update(
                thread_id=run_thread_id, revision_id=revision_id, policy=execution_policy
            )
            execution["name"] = graph_doc.get("name", routine_id)

            # LangGraph only continues an existing checkpoint with ``None`` or
            # an explicit Command.  Supplying fresh seed input repeats completed
            # work, so native toolbar resume is always ``ainvoke(None, thread)``.
            resume_existing = False
            run_input: Any = None
            existing_state: Any = None
            run_context = nested_approval_context(
                [], {**(context or {}), "request_id": run_thread_id}
            )
            if (session_id or resume_thread_id) and checkpointer is not None:
                existing_state = await compiled.aget_state(run_config)
                resume_existing = bool(existing_state and existing_state.values)
                if resume_thread_id:
                    if not resume_existing:
                        raise ValueError("stopped routine checkpoint is missing")
                    if existing_state.interrupts:
                        raise ValueError("stopped routine is awaiting a human approval")
                    if not existing_state.next:
                        raise ValueError("stopped routine checkpoint is already complete")
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
            seed_input["data"]["_execution_policy"] = execution_policy
            seed_input.update(
                {
                    key: value
                    for key, value in seed_input["data"].items()
                    if key not in {"messages", "data", "run_meta"}
                }
            )
            if isinstance(parameters, dict):
                seed_input.update(parameters)
            inherited_scope = current_routine_execution_scope()
            active_controls = (
                inherited_scope.controller
                if inherited_scope is not None and inherited_scope.controller is not None
                else routine_controls
            )
            if inherited_scope is not None:
                await active_controls.register_nested_execution(
                    execution,
                    resume_thread_id=resume_thread_id,
                    allow_paused=resume_existing,
                )
            else:
                await routine_controls.assert_startable(
                    execution,
                    resume_thread_id=resume_thread_id,
                    allow_paused=resume_existing,
                )
            await active_controls.record_started(execution)
            operation = compiled.ainvoke(
                run_input if resume_existing else seed_input,
                config=run_config,
                context=run_context,
                interrupt_after=[state.get("_interactive_target_node")]
                if state.get("_interactive_target_node")
                else None,
            )
            if inherited_scope is None:
                result = await routine_controls.run_graph_execution(
                    execution,
                    operation,
                    resume_thread_id=resume_thread_id,
                    allow_paused=resume_existing,
                )
            else:
                result = await operation
        except RoutineRunControlExit:
            # A user control is already durably reported by the controller.
            # Let it reach the root graph task so no parent settles as success.
            raise
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
        update["_routine_output"] = routine_run_output(selected_graph.output_schema, result)
        if checkpointer is not None:
            snapshot = await compiled.aget_state(run_config)
            update["_routine_paused"] = bool(snapshot.next or snapshot.interrupts)
            update["_routine_pause"] = _pause_details(snapshot.interrupts)
        else:
            update["_routine_paused"] = bool(result.get("__interrupt__"))
            update["_routine_pause"] = _pause_details(result.get("__interrupt__") or ())
        if revision_id is not None:
            update[ROUTINE_REVISIONS_STATE_KEY] = {routine_id: revision_id}
        return update

    async def _execute(state: dict) -> dict:
        resume_thread_id = str(state.get("_routine_resume_thread_id") or "").strip() or None
        if resume_thread_id:
            resumed_routine_id, _revision_id = _parse_routine_thread_id(resume_thread_id)
            if resumed_routine_id != routine_id:
                raise ValueError("resume thread does not belong to this routine")
            session_id = routine_session_from_thread(resume_thread_id, routine_id)
            supplied_session_id = str(state.get("_interactive_session_id") or "").strip()
            if supplied_session_id and supplied_session_id != session_id:
                raise ValueError("resume session does not match its stopped checkpoint")
        else:
            session_id = str(state.get("_interactive_session_id") or "").strip() or uuid.uuid4().hex
        execution = {
            "agent_id": routine_id,
            "session_id": session_id,
            "request_id": state.get("_routine_request_id") or str(uuid.uuid4()),
        }
        if state.get("_routine_parent"):
            execution["parent"] = dict(state["_routine_parent"])
        state = {**state, "_interactive_session_id": session_id, "_routine_execution": execution}
        inherited_scope = current_routine_execution_scope()
        active_controls = (
            inherited_scope.controller
            if inherited_scope is not None and inherited_scope.controller is not None
            else routine_controls
        )
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
        except (RoutineRunControlExit, RoutineRunCancelled):
            # The controller emits a terminal stopped/cancelled event itself;
            # a durable cancelled guard emits nothing because no new execution ran.
            raise
        except Exception as exc:
            if execution.get("thread_id"):
                await active_controls.fail_execution(execution)
            # A resume that failed before owning its checkpoint leaves it stopped.
            await active_controls.release_resume_claim(execution)
            cast(Any, exc).routine_invocation = dict(execution)
            await persist_run_event(state_manager, {**execution, "error": str(exc)}, "failed")
            cast(Any, exc).routine_lifecycle_reported = True
            raise
        finally:
            var_child_runnable_config.reset(token)
        paused = bool(result.get("_routine_paused"))
        status = "paused" if paused else "completed"
        await active_controls.complete_execution(execution, status)
        await persist_run_event(
            state_manager,
            {**execution, **(result.get("_routine_pause") or {})}
            if paused
            else {**execution, "output": result.get("_routine_output")},
            status,
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
    model_profile_resolver: Callable[[str], Awaitable[Any]] | None = None,
    tool_registry: Any = None,
    filesystem_backend: Any | None = None,
    checkpointer: Any | None = None,
    routine_controls: RoutineRunController | None = None,
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
        model_profile_resolver=model_profile_resolver,
        tool_registry=tool_registry,
        filesystem_backend=filesystem_backend,
        checkpointer=checkpointer,
        routine_controls=routine_controls,
    )

    async def _run(state: dict[str, Any]) -> dict[str, Any]:
        # Toolbar resume must not validate against a graph which may have
        # changed since the stopped checkpoint.  The executor verifies the
        # exact pinned revision and native checkpoint instead.
        if state.get("_routine_resume_thread_id"):
            input_schema = None
        else:
            # One snapshot validates the input and runs: a save in between must
            # not validate against one graph and execute another.
            snapshot = await resolve_routine_graph(
                routine_id, state, revision_reader=revision_reader, state_manager=state_manager
            )
            state = {**state, ROUTINE_GRAPH_SNAPSHOT_STATE_KEY: snapshot}
            graph = snapshot[1]
            input_schema = graph.get("input_schema") if isinstance(graph, dict) else None
        if input_schema:
            parameters = state.get("parameters")
            if parameters is None:
                messages = state.get("messages") or []
                description = (
                    messages[0].content
                    if messages and isinstance(messages[0], HumanMessage)
                    else None
                )
                try:
                    parameters = json.loads(description) if isinstance(description, str) else None
                except json.JSONDecodeError:
                    parameters = None
                if not isinstance(parameters, dict):
                    return {
                        "messages": [
                            AIMessage(
                                content=f"Routine '{routine_id}' requires a JSON object task description matching its input_schema."
                            )
                        ]
                    }
            error = validate_against_schema(parameters, input_schema)
            if error is not None:
                return {
                    "messages": [AIMessage(content=f"Routine '{routine_id}' input error: {error}")]
                }
            state = {**state, "parameters": parameters}
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
        if input_schema:
            output = {
                key: value
                for key, value in result.get("data", {}).items()
                if not key.startswith("_")
            }
            update["messages"] = [AIMessage(content=json.dumps({"output": output}, default=str))]
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


def _pause_details(interrupts: Any) -> dict[str, Any]:
    """What a paused run's lifecycle event says it is waiting for.

    The first pending human step's question, allowed answers and approver, so
    a caller (e.g. a workflow raising a pause alert) can tell the user what to
    answer and whether Progena may answer it.
    """
    approvals = pending_approvals(interrupts or ())
    if not approvals:
        return {}
    first = approvals[0]
    return {
        "question": first["message"],
        "allowed_responses": first["allowed_responses"],
        "approver": first["approver"],
    }


async def resume_routine_thread(
    *, thread_id: str, answer: Any, database_url: str | None = None, **kwargs: Any
) -> dict[str, Any]:
    """Answer one still-pending interrupt, shared by UI and Progena chat."""
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
    model_profile_resolver: Callable[[str], Awaitable[Any]] | None = None,
    tool_registry: Any = None,
    filesystem_backend: Any | None = None,
    checkpointer: Any | None = None,
    interrupt_id: str | None = None,
    request_id: str | None = None,
    answered_by: Literal["human", "agent"] = "human",
    routine_controls: RoutineRunController | None = None,
) -> dict[str, Any]:
    """Resume a routine run paused at a human node, given its exact thread_id.

    Shared primitive: this is also what the interactive node-level run/edit
    feature resumes through, since both cases are "continue this checkpointed
    thread with a `Command(resume=...)`" -- only how the answer/edit gets
    produced differs. Recompiles the routine fresh (resume is rare; no need
    to share create_routine_subagent's per-invocation compile cache).
    """
    routine_controls = routine_controls or RoutineRunController(state_manager)
    routine_id, revision_id = _parse_routine_thread_id(thread_id)
    session_id = routine_session_from_thread(thread_id, routine_id)
    await routine_controls.assert_not_cancelled(routine_id, session_id)
    graph_doc: dict[str, Any] | None = None
    if revision_id is not None:
        graph_doc = await revision_reader.fetch(routine_id, revision_id)
    if graph_doc is None:
        graph_doc = await state_manager.get_routine_graph(routine_id)
    if graph_doc is None:
        raise ValueError(f"routine {routine_id!r} has no stored graph to resume against")

    graph = validate_routine_graph_payload(graph_doc)
    instruction_loader = None
    if any(node.kind == "agent" and node.config.instruction is not None for node in graph.nodes):
        instruction_loader, _ = await _routine_instruction_loader(state_manager)
    compiled = compile_routine_graph(
        graph,
        model=model,
        agent_registry=agent_registry,
        routine_registry=routine_registry,
        routine_runner=routine_runner,
        agent_background_runner=agent_background_runner,
        model_profile_resolver=model_profile_resolver,
        tool_registry=tool_registry,
        filesystem_backend=filesystem_backend,
        base_system_prompt=None,
        instruction_loader=instruction_loader,
        checkpointer=checkpointer,
    )
    run_config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    checkpoint = await compiled.aget_state(run_config)
    execution_policy = (checkpoint.values.get("data") or {}).get("_execution_policy")
    if not isinstance(execution_policy, dict):
        raise ValueError("Run has no recorded execution policy")
    compiled = compile_routine_graph(
        graph,
        model=model,
        agent_registry=agent_registry,
        routine_registry=routine_registry,
        routine_runner=routine_runner,
        agent_background_runner=agent_background_runner,
        model_profile_resolver=model_profile_resolver,
        tool_registry=tool_registry,
        filesystem_backend=filesystem_backend,
        instruction_loader=instruction_loader,
        checkpointer=checkpointer,
        execution_policy=PolicyValues.model_validate(execution_policy["values"]),
        cache=_RUNTIME_NODE_CACHE,
        model_profiles=await state_manager.get_model_profiles_config(),
    )
    approval = validate_answer(pending_approvals(checkpoint.interrupts), answer, interrupt_id)
    if answered_by == "agent" and approval["approver"] == "human":
        raise ValueError(
            "This step needs a person's answer: answer it in the Progena AI app "
            "(Routines page or the alert), not through the agent."
        )
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
        "session_id": session_id,
        "request_id": request_id or str(uuid.uuid4()),
        "policy": execution_policy,
    }
    inherited_scope = current_routine_execution_scope()
    active_controls = (
        inherited_scope.controller
        if inherited_scope is not None and inherited_scope.controller is not None
        else routine_controls
    )
    if inherited_scope is not None:
        await active_controls.register_nested_execution(execution, allow_paused=True)
    await active_controls.record_started(execution)
    operation = compiled.ainvoke(
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
    try:
        if inherited_scope is None:
            result = await routine_controls.run_graph_execution(
                execution, operation, allow_paused=True
            )
        else:
            result = await operation
    except (RoutineRunControlExit, RoutineRunCancelled):
        raise
    except Exception as exc:
        await active_controls.fail_execution(execution)
        await persist_run_event(state_manager, {**execution, "error": str(exc)}, "failed")
        cast(Any, exc).routine_lifecycle_reported = True
        raise
    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    status = "paused" if interrupts else "completed"
    await active_controls.complete_execution(execution, status)
    await persist_run_event(
        state_manager,
        {**execution, **_pause_details(interrupts)}
        if interrupts
        else {**execution, "output": routine_run_output(graph.output_schema, result)},
        status,
    )
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
