"""
Subagent definitions for the Progenitor DeepAgent.

Wraps specialized helper capabilities as DeepAgent subagents:
- Code review subagent: lightweight read-only review
- Research subagent: source/documentation lookup and summarization
"""

from __future__ import annotations

import logging

from deepagents.middleware.subagents import SubAgent

logger = logging.getLogger(__name__)


def create_code_review_subagent() -> SubAgent:
    """Create a lightweight code review subagent."""
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
        tools=[],
    )


def create_research_subagent() -> SubAgent:
    """Create a research subagent for information gathering."""
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


def create_progenitor_subagents() -> list[SubAgent]:
    """Create Progenitor's active DeepAgent subagents."""
    subagents = [create_code_review_subagent(), create_research_subagent()]
    logger.info(
        "Created %d subagents: %s",
        len(subagents),
        [s["name"] for s in subagents],
    )
    return subagents
