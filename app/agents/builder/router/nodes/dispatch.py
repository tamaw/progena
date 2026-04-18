"""Router dispatch nodes — invoke the chosen subagent graph."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..state import RouterState

logger = logging.getLogger(__name__)


def _summarize_validation(report: dict[str, Any] | None) -> str:
    if not report:
        return "Validation did not produce a report."
    verdict = report.get("verdict", "unknown")
    score = report.get("score", "n/a")
    summary = report.get("summary", "")
    return f"Validation verdict={verdict} score={score}\n\n{summary}".strip()


def create_dispatch_plan(
    plan_graph: CompiledStateGraph,
) -> Callable[[RouterState], Awaitable[dict[str, Any]]]:
    async def dispatch(state: RouterState) -> dict:
        logger.info("Router dispatch: plan subagent")
        result = await plan_graph.ainvoke(
            {
                "request_text": state.get("request_text", ""),
                "request_context": state.get("request_context", {}),
            }
        )
        planned_build = result.get("planned_build")
        if planned_build is None:
            error = result.get("error") or "Plan subagent produced no plan."
            return {
                "result_text": f"Plan failed: {error}",
                "metadata": {"subagent": "plan", "error": error},
            }
        plan_text = json.dumps(
            {
                **planned_build.plan.model_dump(),
                "reasoning": planned_build.reasoning,
                "planned_at_utc": planned_build.planned_at_utc,
            },
            indent=2,
            sort_keys=True,
        )
        return {
            "planned_build": planned_build,
            "result_text": plan_text,
            "metadata": {"subagent": "plan"},
        }

    return dispatch


def create_dispatch_build(
    build_graph: CompiledStateGraph,
) -> Callable[[RouterState], Awaitable[dict[str, Any]]]:
    async def dispatch(state: RouterState) -> dict:
        planned_build = state.get("planned_build")
        if planned_build is None:
            logger.info("Router dispatch: build subagent (no plan supplied — will plan on demand)")
        else:
            logger.info(
                "Router dispatch: build subagent (work_item_id=%s)",
                planned_build.plan.work_item_id,
            )
        result = await build_graph.ainvoke(
            {
                "request_text": state.get("request_text", ""),
                "request_context": state.get("request_context", {}),
                "planned_build": planned_build,
                "on_planned": state.get("on_planned"),
            }
        )
        return {
            "result_text": result.get("result_text", ""),
            "planned_build": result.get("planned_build") or planned_build,
            "metadata": {
                "subagent": "build",
                "validation": result.get("validation_report"),
                "teardown_path": result.get("teardown_path"),
                "commit_hash": result.get("commit_hash"),
                "pr_url": result.get("pr_url"),
            },
        }

    return dispatch


def create_dispatch_validate(
    validate_graph: CompiledStateGraph,
) -> Callable[[RouterState], Awaitable[dict[str, Any]]]:
    async def dispatch(state: RouterState) -> dict:
        logger.info("Router dispatch: validate subagent")
        result = await validate_graph.ainvoke(
            {
                "request_text": state.get("request_text", ""),
                "request_context": state.get("request_context", {}),
                "planned_build": state.get("planned_build"),
                "evidence_text": state.get("request_text", ""),
            }
        )
        report = result.get("validation_report")
        return {
            "result_text": result.get("result_text") or _summarize_validation(report),
            "metadata": {"subagent": "validate", "validation": report},
        }

    return dispatch
