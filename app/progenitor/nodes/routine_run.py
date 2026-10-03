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
from typing import Any

from langgraph.runtime import Runtime

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
    subagent.py's ``_run``).
    """
    metadata = state.get("current_input_metadata") or {}
    routine_id = str(metadata.get("routine_id") or "").strip()
    if not routine_id:
        return {"cycle_error": "routine_run: missing routine_id in task metadata"}

    routine_runner = runtime.context.routine_runner
    if routine_runner is None:
        return {"cycle_error": "routine_run: routine runner unavailable"}

    session_id = str(metadata.get("session_id") or "").strip() or None
    target_node = str(metadata.get("target_node") or "").strip() or None
    parameters = metadata.get("parameters")

    try:
        invoke_input: dict[str, Any] = {
            "messages": [],
            "_interactive_session_id": session_id,
            "_interactive_target_node": target_node,
        }
        if isinstance(parameters, dict) and parameters:
            invoke_input["parameters"] = parameters
        result = await routine_runner(routine_id, invoke_input)
    except Exception as exc:  # noqa: BLE001 - convert to cycle_error like cycle_node does
        logger.warning("routine_run node failed for %r: %s", routine_id, exc)
        return {"cycle_error": f"routine '{routine_id}' failed: {exc}"}

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
