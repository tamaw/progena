"""Build subagent — execute node (heavy implementation phase)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..prompts import build_execution_prompt, extract_text, serialize_transcript
from ..state import BuildState

logger = logging.getLogger(__name__)


def create_execute_node(
    deep_agent: CompiledStateGraph,
) -> Callable[[BuildState], Awaitable[dict[str, Any]]]:
    async def execute(state: BuildState) -> dict:
        thread_id = state["thread_id"]
        prompt = build_execution_prompt(
            state.get("request_text", ""),
            state.get("request_context", {}),
            state["planned_build"],
        )
        logger.info("Build subagent: execute phase (thread=%s)", thread_id)
        result = await deep_agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
        )
        return {
            "execution_text": extract_text(result),
            "execution_evidence": serialize_transcript(result),
        }

    return execute
