"""Deterministic routine execution -- bypasses the LLM entirely.

Normal routine execution goes through the agent calling ``task(subagent_type=
<routine_id>, ...)``, which means an LLM turn decides *whether* to actually
invoke it -- exactly the failure mode the Agents-page "Run" button used to
hit (a chat prompt asking the model to "report a simulated result" that the
model could narrate instead of executing).

This node sidesteps that: it is only entered when a queued background task's
metadata explicitly marks ``kind: "routine_run"`` (see the
``POST /agent-profiles/{agent_id}/run`` backend route), and it calls the
routine's already-compiled subagent runnable directly -- the same runnable
``task()`` would have invoked, with the same model/registries/checkpointer,
just without an LLM deciding to call it. No chat message, no narration risk.
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
    """Invoke one routine's compiled subagent runnable directly."""
    metadata = state.get("current_input_metadata") or {}
    routine_id = str(metadata.get("routine_id") or "").strip()
    if not routine_id:
        return {"cycle_error": "routine_run: missing routine_id in task metadata"}

    routine_registry = getattr(runtime.context.agent_graph, "routine_registry", None) or {}
    runnable = routine_registry.get(routine_id)
    if runnable is None:
        return {"cycle_error": f"routine_run: unknown or unregistered routine '{routine_id}'"}

    try:
        result = await runnable.ainvoke({"messages": []})
    except Exception as exc:  # noqa: BLE001 - convert to cycle_error like cycle_node does
        logger.warning("routine_run node failed for %r: %s", routine_id, exc)
        return {"cycle_error": f"routine '{routine_id}' failed: {exc}"}

    if isinstance(result, dict) and result.get("__interrupt__"):
        # A human node paused this run. It's checkpointed and resumable, but
        # nothing consumes that yet -- tracked separately, not a failure here.
        logger.info("routine_run node interrupted for %r (human node)", routine_id)
    return {}
