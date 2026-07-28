"""
Subagent definitions for the Progenitor DeepAgent.

Wraps specialized helper capabilities as DeepAgent subagents:
- Hello-world subagent: deterministic delegation smoke test
- Code review subagent: lightweight read-only review
- Research subagent: source/documentation lookup and summarization
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.async_subagents import AsyncSubAgent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent

from ..async_subagents import HELLO_WORLD_ASYNC_ASSISTANT_ID
from .middleware import AuditLoggingMiddleware

logger = logging.getLogger(__name__)


def _subagent_middleware(log_sink: Any | None) -> list[Any]:
    """Build the per-subagent middleware stack.

    Subagents get zero middleware unless a spec sets its own ``middleware``
    key (deepagents wires per-subagent and top-level middleware separately),
    so audit logging must be attached explicitly here to make nested tool
    calls visible on the SSE stream.
    """
    return [AuditLoggingMiddleware(log_sink=log_sink)]


def create_hello_world_subagent(*, log_sink: Any | None = None) -> SubAgent:
    """Create a deterministic subagent for delegation smoke tests."""
    return SubAgent(
        name="hello-world",
        description=(
            "Returns the fixed token HELLO_FROM_SUBAGENT for delegation smoke tests. "
            "Use only when explicitly asked to verify that subagent delegation is working."
        ),
        system_prompt=(
            "You are a hello-world subagent used only for delegation tests. "
            "Reply with exactly HELLO_FROM_SUBAGENT and nothing else. "
            "Do not call tools."
        ),
        tools=[],
        middleware=_subagent_middleware(log_sink),
    )


def create_code_review_subagent(*, log_sink: Any | None = None) -> SubAgent:
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
        middleware=_subagent_middleware(log_sink),
    )


def create_research_subagent(*, log_sink: Any | None = None) -> SubAgent:
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
        middleware=_subagent_middleware(log_sink),
    )


def create_general_purpose_subagent(*, log_sink: Any | None = None) -> SubAgent:
    """Create an explicit general-purpose subagent spec.

    deepagents auto-adds its own default "general-purpose" subagent unless a
    spec with that name is already supplied -- an explicit spec is the only
    way to opt it into our audit-logging middleware, since the auto-added
    default never receives it. Without this, delegations to general-purpose
    (deepagents' catch-all, used whenever no more specific subagent fits)
    would be the one subagent to emit no nested tool lifecycle events.
    """
    spec: SubAgent = {**GENERAL_PURPOSE_SUBAGENT}
    spec["middleware"] = _subagent_middleware(log_sink)
    return spec


def create_hello_world_async_subagent(http_base_url: str) -> AsyncSubAgent:
    """Create the deterministic async subagent used for background delegation tests."""
    return AsyncSubAgent(
        name="hello-world-async",
        description=(
            "Runs in the background and later returns the fixed token "
            "HELLO_FROM_ASYNC_SUBAGENT. Use when explicitly asked to test async "
            "subagent delegation or background completion callbacks."
        ),
        graph_id=HELLO_WORLD_ASYNC_ASSISTANT_ID,
        url=f"{http_base_url.rstrip('/')}/agent-protocol",
    )


def create_progenitor_sync_subagents(*, log_sink: Any | None = None) -> list[SubAgent]:
    """Create Progenitor's inline DeepAgent subagents."""
    return [
        create_hello_world_subagent(log_sink=log_sink),
        create_code_review_subagent(log_sink=log_sink),
        create_research_subagent(log_sink=log_sink),
        create_general_purpose_subagent(log_sink=log_sink),
    ]


def create_progenitor_async_subagents(http_base_url: str) -> list[AsyncSubAgent]:
    """Create Progenitor's background async subagents."""
    return [create_hello_world_async_subagent(http_base_url)]


def create_progenitor_subagents(
    http_base_url: str, *, log_sink: Any | None = None
) -> list[SubAgent | AsyncSubAgent]:
    """Create Progenitor's active DeepAgent subagents."""
    subagents: list[SubAgent | AsyncSubAgent] = [
        *create_progenitor_sync_subagents(log_sink=log_sink),
        *create_progenitor_async_subagents(http_base_url),
    ]
    logger.info(
        "Created %d subagents: %s",
        len(subagents),
        [s["name"] for s in subagents],
    )
    return subagents
