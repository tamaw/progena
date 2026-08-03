"""Heartbeat node: scan declared repos.json entries for live git state."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime

from ..repos import aggregate_workspace_signal, scan_declared_repos
from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext

logger = logging.getLogger(__name__)


async def refresh_repo_scan_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Scan declared repositories' git state when the heartbeat marks it due.

    Reads the operator-declared ``repos.json`` document, scans each entry's
    ``local_path`` on disk (branch, dirty count, commits, worktrees), and
    persists the result as the ``repo_scan`` document. The backend reads that
    document read-through for the Repositories page; the agent never edits it
    directly (``repos.json`` - the declared entries - is the only agent-facing
    surface for this feature).
    """
    context = runtime.context
    if context.state_manager is None:
        return {}
    if "repo_scan" not in context.due_maintenance:
        return {}

    try:
        declared = await context.state_manager.get_repos()
        raw_repos = declared.get("repos") if isinstance(declared, dict) else None
        repos = raw_repos if isinstance(raw_repos, dict) else {}
        scans = await scan_declared_repos(repos, worktree_dir=context.worktree_dir)
        signal = aggregate_workspace_signal(scans)
        payload = {
            "scanned_at": utcnow_isoformat(),
            "repos": {repo_id: scan.to_dict() for repo_id, scan in scans.items()},
            "workspace_signal": signal.to_dict(),
        }
        await context.state_manager.save_repo_scan(payload)
        summary: dict[str, Any] = {"scanned": len(scans), "linked_repos": signal.linked_repos}
    except Exception as exc:  # pragma: no cover - best-effort heartbeat tick
        logger.warning("Failed to scan declared repos: %s", exc)
        summary = {"error": str(exc)}

    return {
        "last_repo_scan_at": utcnow_isoformat(),
        "repo_scan_summary": summary,
    }
