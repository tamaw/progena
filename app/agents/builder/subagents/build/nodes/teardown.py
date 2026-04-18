"""Build subagent — teardown nodes (success path + cleanup-only fail path)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from ..prompts import (
    build_teardown_failed_prompt,
    build_teardown_prompt,
    extract_text,
)
from ..state import BuildState

logger = logging.getLogger(__name__)


def _extract_pr_url(text: str) -> str | None:
    import re

    m = re.search(r"https://github\.com/\S+/pull/\d+", text)
    return m.group(0) if m else None


def _extract_commit(text: str) -> str | None:
    import re

    m = re.search(r"(?:SHA|sha|commit|hash)[:\s]+([0-9a-f]{7,40})\b", text)
    return m.group(1) if m else None


def create_teardown_node(
    deep_agent: CompiledStateGraph,
) -> Callable[[BuildState], Awaitable[dict[str, Any]]]:
    """Success-path teardown: push and create a PR if a commit exists."""

    async def teardown(state: BuildState) -> dict:
        thread_id = state["thread_id"]
        prompt = build_teardown_prompt(
            state.get("request_text", ""),
            state.get("request_context", {}),
            state["planned_build"],
            state.get("validation_report"),
        )
        logger.info("Build subagent: teardown[success] phase (thread=%s)", thread_id)
        result = await deep_agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
        )
        teardown_text = extract_text(result)
        return {
            "teardown_text": teardown_text,
            "teardown_path": "success",
            "result_text": teardown_text,
            "pr_url": _extract_pr_url(teardown_text),
            "commit_hash": _extract_commit(teardown_text),
        }

    return teardown


def create_teardown_failed_node(
    deep_agent: CompiledStateGraph,
) -> Callable[[BuildState], Awaitable[dict[str, Any]]]:
    """Failure-path teardown: cleanup only, never push or open a PR."""

    async def teardown_failed(state: BuildState) -> dict:
        thread_id = state["thread_id"]
        prompt = build_teardown_failed_prompt(
            state.get("request_text", ""),
            state.get("request_context", {}),
            state["planned_build"],
            state.get("validation_report"),
        )
        logger.info("Build subagent: teardown[failed] phase (thread=%s)", thread_id)
        result = await deep_agent.ainvoke(
            {"messages": [{"role": "user", "content": prompt}]},
            {"configurable": {"thread_id": thread_id}},
        )
        teardown_text = extract_text(result)
        report = state.get("validation_report") or {}
        verdict = report.get("verdict", "fail")
        result_text = (
            f"Build aborted by validate subagent (verdict={verdict}).\n\n"
            f"{report.get('summary', '')}\n\n"
            f"Cleanup result:\n{teardown_text}"
        ).strip()
        return {
            "teardown_text": teardown_text,
            "teardown_path": "failed",
            "result_text": result_text,
            "pr_url": None,
            "commit_hash": None,
        }

    return teardown_failed
