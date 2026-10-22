"""Parent agent state channel for pinned routine graph revisions.

Extends the DeepAgents state schema with ``routine_revisions``, so a chat
thread that delegates to a routine (via
:func:`src.progena.routines.subagent.create_routine_subagent`) keeps
resolving the graph revision it started on, even after the routine is
edited again, while a new thread picks up the latest revision.
"""

from __future__ import annotations

from typing import Annotated

from deepagents.graph import DeepAgentState


def merge_routine_revisions(
    existing: dict[str, str] | None, update: dict[str, str] | None
) -> dict[str, str]:
    """Merge per-routine pins so one routine's pin never clobbers another's."""
    merged = dict(existing or {})
    merged.update(update or {})
    return merged


class ProgenaAgentState(DeepAgentState):
    # Field name must stay the literal below (TypedDict annotations can't use a
    # variable) — kept in sync with ROUTINE_REVISIONS_STATE_KEY in
    # src.progena.routines.subagent.
    routine_revisions: Annotated[dict[str, str], merge_routine_revisions]
