"""Build subagent state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from ...client import PlannedBuild


class BuildState(TypedDict, total=False):
    """State threaded through ``setup -> execute -> validate -> teardown``.

    ``execution_text`` is the assistant's narrative summary; ``execution_evidence``
    is the structured transcript (tool calls + tool results) used by the
    validate subagent so the verdict reflects real evidence.

    ``on_planned`` is an optional async callback fired exactly once after the
    setup node materialises a plan (or immediately, if a plan was supplied by
    the caller). Errors raised by the callback are swallowed; planning
    completion is purely informational for downstream observers.
    """

    request_text: str
    request_context: dict[str, Any]
    planned_build: PlannedBuild
    thread_id: str
    setup_text: str
    execution_text: str
    execution_evidence: str
    validation_report: dict[str, Any] | None
    teardown_text: str
    teardown_path: str
    result_text: str
    commit_hash: str | None
    pr_url: str | None
    on_planned: Callable[[PlannedBuild], Awaitable[None]] | None
