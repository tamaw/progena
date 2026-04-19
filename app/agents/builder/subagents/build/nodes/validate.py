"""Build subagent — invoke the validate subagent against fresh execution evidence."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ....client import PlannedBuild
from ..state import BuildState

logger = logging.getLogger(__name__)


def _require_planned_build(state: BuildState) -> PlannedBuild:
    planned_build = state.get("planned_build")
    if planned_build is None:
        raise ValueError("Build validate node requires planned_build")
    return planned_build


def create_validate_node(
    validate_graph: CompiledStateGraph,
) -> Callable[[BuildState], Awaitable[dict[str, Any]]]:
    """Call the validate subagent on the just-finished execution output.

    Validation runs *before* teardown so the verdict reflects the actual
    execution evidence rather than post-cleanup state.
    """

    async def validate(state: BuildState) -> dict:
        evidence = state.get("execution_evidence") or state.get("execution_text") or ""
        planned_build = _require_planned_build(state)
        logger.info(
            "Build subagent: validate phase (thread=%s evidence_len=%d)",
            state.get("thread_id", "?"),
            len(evidence),
        )
        result = await validate_graph.ainvoke(
            {
                "request_text": state.get("request_text", ""),
                "request_context": state.get("request_context", {}),
                "planned_build": planned_build,
                "evidence_text": evidence,
            }
        )
        report = result.get("validation_report")
        return {"validation_report": report}

    return validate
