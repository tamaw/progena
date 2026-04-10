"""
Subagent definitions for the Progenitor DeepAgent.

Wraps Progenitor's specialized capabilities as DeepAgent subagents:
- Build subagent: inner build loop (OBSERVE→THINK→PLAN→BUILD→EXECUTE→VERIFY→LEARN)
- Code review subagent: lightweight review using cheaper model
- Research subagent: web search and documentation lookup
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent, SubAgent

logger = logging.getLogger(__name__)


def create_build_subagent(
    inner_loop_graph: Any | None = None,
) -> CompiledSubAgent | None:
    """Wrap the inner build loop as a CompiledSubAgent.

    The inner build loop (OBSERVE→THINK→PLAN→BUILD→MERGE→EXECUTE→VERIFY→LEARN)
    is Progenitor's most sophisticated component. Wrapping it as a CompiledSubAgent
    gives it context isolation — build output doesn't clutter the main conversation.

    Args:
        inner_loop_graph: A compiled LangGraph graph for the inner build loop.
                          If None, the build subagent is not available.

    Returns:
        CompiledSubAgent wrapping the build loop, or None if not configured.
    """
    if inner_loop_graph is None:
        logger.info("No inner loop graph provided — build subagent not available")
        return None

    return CompiledSubAgent(
        name="build-agent",
        description=(
            "Executes the full iterative build loop for code generation tasks. "
            "Handles OBSERVE→THINK→PLAN→BUILD→MERGE→EXECUTE→VERIFY→LEARN cycles "
            "with git worktree isolation, anti-criteria learning, and weighted "
            "verification scoring. Use for any task requiring code changes, "
            "agent building, or tool creation."
        ),
        runnable=inner_loop_graph,
    )


def create_code_review_subagent() -> SubAgent:
    """Create a lightweight code review subagent.

    Uses a simpler model for cost-efficient code review. Reviews diffs,
    suggests improvements, and checks for common issues.
    """
    return SubAgent(
        name="code-reviewer",
        description=(
            "Reviews code changes and provides feedback. "
            "Analyzes diffs, identifies bugs, suggests improvements, "
            "and checks for style/pattern compliance. Use for PR reviews "
            "or pre-commit code quality checks."
        ),
        system_prompt=(
            "You are a code reviewer for the Progenitor project. "
            "Review the provided code changes and give concise, actionable feedback. "
            "Focus on: bugs, security issues, performance problems, and pattern violations. "
            "Be constructive and specific. Reference line numbers when possible."
        ),
        tools=[],  # Review is read-only — no tools needed
    )


def create_research_subagent() -> SubAgent:
    """Create a research subagent for information gathering.

    Delegates research tasks to a subagent with context isolation,
    so research results are summarized before returning to the main agent.
    Tool access is inherited from the main DeepAgent.
    """
    return SubAgent(
        name="researcher",
        description=(
            "Researches topics using available tools. "
            "Gathers information, reads documentation, and summarizes findings. "
            "Use when the main agent needs background research before making decisions."
        ),
        system_prompt=(
            "You are a research assistant. Your job is to gather information "
            "and provide clear, concise summaries. Read files, search code, "
            "and synthesize findings. Always cite your sources (file paths, "
            "line numbers). Return a structured summary."
        ),
    )


def create_progenitor_subagents(
    inner_loop_graph: Any | None = None,
) -> list[CompiledSubAgent | SubAgent]:
    """Create all Progenitor subagents.

    Args:
        inner_loop_graph: Compiled inner build loop graph (optional).

    Returns:
        List of subagents to register with the DeepAgent.
    """
    subagents: list[CompiledSubAgent | SubAgent] = []

    # Build subagent (only if inner loop is available)
    build = create_build_subagent(inner_loop_graph)
    if build is not None:
        subagents.append(build)

    # Always-available subagents
    subagents.append(create_code_review_subagent())
    subagents.append(create_research_subagent())

    logger.info(
        "Created %d subagents: %s",
        len(subagents),
        [s["name"] for s in subagents],
    )
    return subagents
