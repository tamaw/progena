"""Plan subagent — turn a raw request into a structured BuildPlan.

Inspired by the planner ideas in `app/agents/builder_agent/nodes/plan.py`
(spec shaping, structured output, downstream-execution-ready) but
implemented standalone using the local ``BuilderClient``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ....client import BuilderClient, BuildPlan, PlannedBuild
from ....prompts import load_prompt
from ..state import PlanState

logger = logging.getLogger(__name__)


def create_shape_request_node(
    client: BuilderClient,
) -> Callable[[PlanState], Awaitable[dict[str, Any]]]:
    """Return a node that shapes the request into a PlannedBuild via Azure OpenAI."""

    system_prompt = load_prompt("plan_system.md").strip()

    async def shape_request(state: PlanState) -> dict:
        request_text = (state.get("request_text") or "").strip()
        if not request_text:
            return {"error": "Empty request text — cannot plan."}

        context = state.get("request_context") or {}
        context_json = json.dumps(context, indent=2, sort_keys=True) if context else "{}"
        planned_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        user_message = (
            f"Request:\n{request_text}\n\n"
            f"Caller context (JSON):\n{context_json}\n\n"
            f"Current UTC timestamp: {planned_at_utc}"
        )
        try:
            plan, reasoning = await client.chat_structured_with_reasoning(
                messages=[{"role": "user", "content": user_message}],
                response_model=BuildPlan,
                schema_name="builder_build_plan",
                system_prompt=system_prompt,
                max_tokens=8192,
            )
        except Exception as exc:  # noqa: BLE001 — surface as state error
            logger.exception("Plan subagent failed to produce a plan")
            return {"error": str(exc)}

        planned_build = PlannedBuild(
            plan=plan,
            reasoning=reasoning,
            planned_at_utc=planned_at_utc,
        )
        logger.info(
            "Plan subagent produced plan task_name=%s branch=%s work_item_id=%s",
            plan.task_name,
            plan.branch_name,
            plan.work_item_id,
        )
        return {"planned_build": planned_build, "error": None}

    return shape_request
