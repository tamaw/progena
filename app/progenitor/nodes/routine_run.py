"""Deterministic routine execution -- bypasses the LLM entirely.

Normal routine execution goes through the agent calling ``task(subagent_type=
<routine_id>, ...)``, which means an LLM turn decides *whether* to actually
invoke it -- exactly the failure mode the Agents-page "Run" button used to
hit (a chat prompt asking the model to "report a simulated result" that the
model could narrate instead of executing).

This node sidesteps that: it is only entered when a queued background task's
metadata explicitly marks ``kind: "routine_run"`` (see the
``POST /agent-profiles/{agent_id}/run`` backend route), and it invokes the
runtime's ephemeral Postgres-backed routine runner directly -- without an
in-memory registry of compiled routines and without an LLM deciding to call
it. No chat message, no narration risk.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from ..routines.events import persist_run_event
from ..state.models import ProgenitorState
from .context import HeartbeatContext

logger = logging.getLogger(__name__)


async def routine_run_node(
    state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Invoke one routine's compiled subagent runnable directly.

    ``metadata.session_id``/``metadata.target_node`` (optional) thread
    through to the runnable as ``_interactive_session_id``/
    ``_interactive_target_node`` -- a stable session_id reused across
    repeated calls resumes the same checkpointed thread instead of starting
    a fresh run each time (see subagent.py's ``_run``); target_node runs
    that node and then stops via LangGraph's ``interrupt_after``.
    Omitting both is a normal, complete, one-shot run -- what the plain
    "Run" button sends today. ``metadata.parameters`` (optional) is a flat
    ``{name: value}`` object matching the routine's declared
    ``input_schema.properties`` -- passed straight through to the runnable,
    which threads it into the seed node's top-level invoke keys (see
    subagent.py's ``_run``). ``metadata.message`` (optional) is the human
    turn for a routine that declares no inputs (a workflow routine node).
    """
    metadata = state.get("current_input_metadata") or {}
    routine_id = str(metadata.get("routine_id") or "").strip()

    async def failed(message: str, exc: Exception | None = None) -> dict[str, Any]:
        if not getattr(exc, "routine_lifecycle_reported", False):
            await persist_run_event(
                getattr(runtime.context, "state_manager", None),
                {
                    "agent_id": routine_id,
                    "session_id": metadata.get("session_id"),
                    "request_id": metadata.get("request_id") or str(uuid.uuid4()),
                    "error": message,
                },
                "failed",
            )
        return {"cycle_error": message}

    if not routine_id:
        return await failed("routine_run: missing routine_id in task metadata")

    if metadata.get("operation") == "resume":
        missing = [
            key
            for key in ("thread_id", "answer", "interrupt_id", "request_id")
            if key not in metadata
        ]
        if missing:
            return await failed(
                f"routine_run: resume metadata missing required field(s): {', '.join(missing)}"
            )
        resumer = getattr(runtime.context, "routine_resumer", None)
        if resumer is None:
            return await failed("routine_run: routine resume unavailable")
        try:
            await resumer(
                metadata["thread_id"],
                metadata["answer"],
                interrupt_id=metadata["interrupt_id"],
                request_id=metadata["request_id"],
            )
        except Exception as exc:
            return await failed(str(exc), exc)
        return {}

    routine_runner = runtime.context.routine_runner
    if routine_runner is None:
        return await failed("routine_run: routine runner unavailable")

    session_id = str(metadata.get("session_id") or "").strip() or None
    target_node = str(metadata.get("target_node") or "").strip() or None
    parameters = metadata.get("parameters")
    message = metadata.get("message")

    try:
        invoke_input: dict[str, Any] = {
            # A routine without declared inputs is driven by one human turn.
            "messages": [HumanMessage(content=message)] if isinstance(message, str) else [],
            "_interactive_session_id": session_id,
            "_interactive_target_node": target_node,
            "_routine_request_id": metadata.get("request_id"),
        }
        if isinstance(parameters, dict) and parameters:
            invoke_input["parameters"] = parameters
        result = await routine_runner(routine_id, invoke_input)
    except Exception as exc:  # noqa: BLE001 - convert to cycle_error like cycle_node does
        logger.warning("routine_run node failed for %r: %s", routine_id, exc)
        return await failed(f"routine '{routine_id}' failed: {exc}", exc)

    messages = result.get("messages") if isinstance(result, dict) else None
    if messages:
        # subagent.py's _run already converts a human-node interrupt into an
        # informative message (question + resume thread_id) rather than
        # returning a raw __interrupt__ -- log its final message so an
        # interrupted or failed run is at least visible somewhere until the
        # Runs panel / node UI surfaces it directly.
        logger.info(
            "routine_run node finished for %r: %s",
            routine_id,
            getattr(messages[-1], "content", messages[-1]),
        )
    return {}
