"""Build subagent — setup node (plan-on-demand + worktree/branch preparation)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ....client import BuilderClient
from ..prompts import build_setup_prompt, extract_text
from ..state import BuildState

logger = logging.getLogger(__name__)


def create_setup_node(
    deep_agent: CompiledStateGraph,
    client: BuilderClient,
) -> Callable[[BuildState], Awaitable[dict[str, Any]]]:
    async def setup(state: BuildState) -> dict:
        thread_id = state.get("thread_id") or f"builder_{uuid.uuid4().hex[:12]}"
        request_text = state.get("request_text", "")
        request_ctx = state.get("request_context", {})

        update: dict[str, Any] = {"thread_id": thread_id}

        planned_build = state.get("planned_build")
        if planned_build is None:
            logger.info("Build subagent: setup phase planning on demand (thread=%s)", thread_id)
            planned_build = await client.plan_build(request_text, request_ctx)
            update["planned_build"] = planned_build
            on_planned = state.get("on_planned")
            if on_planned is not None:
                try:
                    await on_planned(planned_build)
                except Exception:  # noqa: BLE001
                    logger.exception("Build subagent: on_planned callback raised; continuing")

        prompt = build_setup_prompt(request_text, request_ctx, planned_build)
        logger.info("Build subagent: setup phase (thread=%s)", thread_id)
        result = await deep_agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
        )
        update["setup_text"] = extract_text(result)
        return update

    return setup
